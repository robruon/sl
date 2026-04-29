#define _GNU_SOURCE
/*
 * arc_runtime.c  ·  ARC runtime — corrected implementation
 *
 * Root causes fixed vs previous version:
 *  1. lang_cycle_candidate does NOT retain — retaining inflates refcounts,
 *     breaks trial-deletion, and prevents cascade releases in ownership chains.
 *  2. Colour-reset loop ran AFTER free (use-after-free). Now categories are
 *     determined and surviving objects are reset BEFORE any freeing.
 *  3. Periodic GC trigger fired at n=0 (first call). Now triggers at n+1 ≡ 0.
 *  4. Collect phase is two-pass: call all dtors, THEN bulk-free.
 *     During dtors the _collecting flag makes lang_release on WHITE objects
 *     a no-op, preventing use-after-free inside destructor chains.
 *  5. _weak_side_release removes obj from _purple before freeing, so the GC
 *     never holds a dangling pointer to a normally-freed candidate.
 */

#include "arc_runtime.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <assert.h>

/* ── Statistics ─────────────────────────────────────────────────── */
static _Atomic uint64_t _stat_allocs   = 0;
static _Atomic uint64_t _stat_frees    = 0;
static _Atomic uint64_t _stat_retains  = 0;
static _Atomic uint64_t _stat_releases = 0;
static _Atomic uint64_t _stat_cycles   = 0;
static _Atomic uint64_t _stat_cyc_obj  = 0;

/* ── Cycle-collector state ───────────────────────────────────────── */
#define MAX_CANDIDATES 8192
static LangObj  *_purple[MAX_CANDIDATES];
static size_t    _purple_count = 0;

static LangObj  *_white_buf[MAX_CANDIDATES];
static size_t    _white_count = 0;

static _Atomic bool _collecting = false;

#define CYCLE_TRIGGER 2048
static _Atomic uint64_t _release_counter = 0;

/* ═══ Internal: weak-side decrement ════════════════════════════════ */

static void _weak_side_release(LangObj *obj) {
    /* Remove from candidate set BEFORE freeing to prevent dangling ptr. */
    int32_t c = atomic_load_explicit(&obj->color, memory_order_relaxed);
    if (c == ARC_PURPLE || c == ARC_GRAY) {
        for (size_t i = 0; i < _purple_count; i++) {
            if (_purple[i] == obj) {
                _purple[i] = _purple[--_purple_count];
                break;
            }
        }
    }
    int64_t prev = atomic_fetch_sub_explicit(
        &obj->weak_count, 1, memory_order_acq_rel);
    if (prev == 1) {
        atomic_fetch_add_explicit(&_stat_frees, 1, memory_order_relaxed);
        free(obj);
    }
}

/* ═══ Allocation ════════════════════════════════════════════════════ */

LangObj *lang_alloc(size_t size, VTable *vtable) {
    assert(size >= sizeof(LangObj));
    LangObj *obj = calloc(1, size);
    if (!obj) { fprintf(stderr, "arc: OOM (%zu bytes)\n", size); abort(); }
    obj->vtable = vtable;
    atomic_init(&obj->refcount,   1);
    atomic_init(&obj->weak_count, 1);
    atomic_init(&obj->color,      ARC_BLACK);
    atomic_fetch_add_explicit(&_stat_allocs, 1, memory_order_relaxed);
    return obj;
}

LangObj *lang_alloc_copy(size_t size, VTable *vtable, const void *src) {
    LangObj *obj = lang_alloc(size, vtable);
    memcpy((char *)obj + sizeof(LangObj),
           (const char *)src + sizeof(LangObj),
           size - sizeof(LangObj));
    return obj;
}

/* ═══ Retain ════════════════════════════════════════════════════════ */

LangObj *lang_retain(LangObj *obj) {
    atomic_fetch_add_explicit(&obj->refcount, 1, memory_order_relaxed);
    atomic_fetch_add_explicit(&_stat_retains, 1, memory_order_relaxed);
    return obj;
}

/* ═══ Release ═══════════════════════════════════════════════════════ */

void lang_release(LangObj *obj) {
    /* During collection: releasing a WHITE object is a no-op.
     * The bulk-free pass handles it; destructors must not double-free. */
    if (atomic_load_explicit(&_collecting, memory_order_relaxed)) {
        int32_t c = atomic_load_explicit(&obj->color, memory_order_relaxed);
        if (c == ARC_WHITE || c == ARC_DEAD) return;
    }

    atomic_fetch_add_explicit(&_stat_releases, 1, memory_order_relaxed);

    int64_t prev = atomic_fetch_sub_explicit(
        &obj->refcount, 1, memory_order_acq_rel);

    assert(prev >= 1 && "lang_release: double-release (refcount < 0)");

    if (prev == 1) {
        /* rc reached 0 — object is dead.
         * Do NOT change color here: if color == ARC_PURPLE the object is in
         * _purple and _weak_side_release must find and remove it before free. */
        if (obj->vtable && obj->vtable->destructor)
            obj->vtable->destructor(obj);
        _weak_side_release(obj);

    } else {
        /* rc still > 0 — possible cycle root */
        int32_t old = ARC_BLACK;
        if (atomic_compare_exchange_strong_explicit(
                &obj->color, &old, ARC_PURPLE,
                memory_order_relaxed, memory_order_relaxed)) {
            lang_cycle_candidate(obj);
        }
        /* Trigger GC every CYCLE_TRIGGER else-branch releases.
         * n+1 is used so the first release (n=0) does NOT trigger. */
        uint64_t n = atomic_fetch_add_explicit(
            &_release_counter, 1, memory_order_relaxed) + 1;
        if ((n % CYCLE_TRIGGER) == 0)
            lang_collect_cycles();
    }
}

/* ═══ Weak references ═══════════════════════════════════════════════ */

WeakRef *lang_weak_ref(LangObj *obj) {
    WeakRef *ref = malloc(sizeof(WeakRef));
    if (!ref) abort();
    atomic_init(&ref->target, obj);
    atomic_fetch_add_explicit(&obj->weak_count, 1, memory_order_relaxed);
    return ref;
}

LangObj *lang_weak_deref(const WeakRef *ref) {
    LangObj *obj = atomic_load_explicit(
        &((WeakRef *)ref)->target, memory_order_acquire);
    if (!obj) return NULL;
    int64_t rc = atomic_load_explicit(&obj->refcount, memory_order_relaxed);
    while (rc > 0) {
        if (atomic_compare_exchange_weak_explicit(
                &obj->refcount, &rc, rc + 1,
                memory_order_acq_rel, memory_order_relaxed)) {
            atomic_fetch_add_explicit(&_stat_retains, 1, memory_order_relaxed);
            return obj;
        }
    }
    return NULL;
}

void lang_weak_release(WeakRef *ref) {
    LangObj *obj = atomic_load_explicit(&ref->target, memory_order_acquire);
    free(ref);
    if (obj) _weak_side_release(obj);
}

/* ═══ Cycle collector — Bacon-Rajan three phase ════════════════════ */

void lang_cycle_candidate(LangObj *obj) {
    /* NO retain — see file header. _weak_side_release removes before free. */
    if (_purple_count >= MAX_CANDIDATES) return;
    for (size_t i = 0; i < _purple_count; i++)
        if (_purple[i] == obj) return;
    _purple[_purple_count++] = obj;
}

/* ── Phase 1: mark gray (trial deletion) ──── */

static void _mark_gray(LangObj *);
static void _mark_gray_cb(LangObj *c, void *x) { (void)x; _mark_gray(c); }

static void _mark_gray(LangObj *obj) {
    if (!obj) return;
    if (atomic_load_explicit(&obj->color, memory_order_relaxed) == ARC_GRAY)
        return;  /* already visited — do NOT double-decrement */
    atomic_store_explicit(&obj->color, ARC_GRAY, memory_order_relaxed);
    if (obj->vtable && obj->vtable->visit_children)
        obj->vtable->visit_children(obj, _mark_gray_cb, NULL);
    /* Decrement after visiting children so children see the right color */
    atomic_fetch_sub_explicit(&obj->refcount, 1, memory_order_relaxed);
}

/* ── Phase 2: scan (restore live, mark garbage white) ──── */

static void _scan(LangObj *);
static void _scan_cb(LangObj *c, void *x)      { (void)x; _scan(c); }
static void _restore_cb(LangObj *c, void *x) {
    (void)x;
    atomic_fetch_add_explicit(&c->refcount, 1, memory_order_relaxed);
    int32_t color = atomic_load_explicit(&c->color, memory_order_relaxed);
    if (color == ARC_BLACK) return;     /* already restored — stop recursion */
    /* Object was GRAY or WHITE but has an incoming live reference — restore it */
    atomic_store_explicit(&c->color, ARC_BLACK, memory_order_relaxed);
    if (c->vtable && c->vtable->visit_children)
        c->vtable->visit_children(c, _restore_cb, NULL);
}

static void _scan(LangObj *obj) {
    if (!obj) return;
    if (atomic_load_explicit(&obj->color, memory_order_relaxed) != ARC_GRAY)
        return;
    if (atomic_load_explicit(&obj->refcount, memory_order_relaxed) > 0) {
        atomic_store_explicit(&obj->color, ARC_BLACK, memory_order_relaxed);
        if (obj->vtable && obj->vtable->visit_children)
            obj->vtable->visit_children(obj, _restore_cb, NULL);
    } else {
        atomic_store_explicit(&obj->color, ARC_WHITE, memory_order_relaxed);
        if (obj->vtable && obj->vtable->visit_children)
            obj->vtable->visit_children(obj, _scan_cb, NULL);
    }
}

/* ── Phase 3: gather white, dtor, free ──── */

static void _collect_white_cb(LangObj *child, void *ctx) {
    (void)ctx;
    if (!child) return;
    if (atomic_load_explicit(&child->color, memory_order_relaxed) != ARC_WHITE)
        return;
    if (_white_count >= MAX_CANDIDATES) return;
    atomic_store_explicit(&child->color, ARC_DEAD, memory_order_relaxed);
    _white_buf[_white_count++] = child;
    if (child->vtable && child->vtable->visit_children)
        child->vtable->visit_children(child, _collect_white_cb, NULL);
}

void lang_collect_cycles(void) {
    if (_purple_count == 0) return;

    /* Phase 1 */
    for (size_t i = 0; i < _purple_count; i++)
        _mark_gray(_purple[i]);

    /* Phase 2 */
    for (size_t i = 0; i < _purple_count; i++)
        _scan(_purple[i]);

    /* Phase 3a: categorise — ARC_DEAD for garbage, BLACK for survivors.
     * ALL colour writes happen BEFORE any free() call.                  */
    _white_count = 0;
    for (size_t i = 0; i < _purple_count; i++) {
        LangObj *obj = _purple[i];
        int32_t  c   = atomic_load_explicit(&obj->color, memory_order_relaxed);
        if (c == ARC_WHITE) {
            atomic_store_explicit(&obj->color, ARC_DEAD, memory_order_relaxed);
            _white_buf[_white_count++] = obj;
            if (obj->vtable && obj->vtable->visit_children)
                obj->vtable->visit_children(obj, _collect_white_cb, NULL);
        } else if (c != ARC_DEAD) {
            /* Surviving candidate — ARC_DEAD objects are already in _white_buf,
             * never overwrite them back to BLACK or we lose the guard. */
            atomic_store_explicit(&obj->color, ARC_BLACK, memory_order_relaxed);
        }
    }
    _purple_count = 0;
    size_t n_collected = _white_count;

    /* Phase 3b: call destructors while objects are still in memory.
     * _collecting=true makes lang_release on WHITE sibling objects a no-op. */
    atomic_store_explicit(&_collecting, true, memory_order_release);
    for (size_t i = 0; i < n_collected; i++) {
        LangObj *obj = _white_buf[i];
        if (obj->vtable && obj->vtable->destructor)
            obj->vtable->destructor(obj);
    }
    atomic_store_explicit(&_collecting, false, memory_order_release);

    /* Phase 3c: bulk-free */
    for (size_t i = 0; i < n_collected; i++) {
        atomic_fetch_add_explicit(&_stat_frees, 1, memory_order_relaxed);
        free(_white_buf[i]);
    }

    atomic_fetch_add_explicit(&_stat_cycles,  1,           memory_order_relaxed);
    atomic_fetch_add_explicit(&_stat_cyc_obj, n_collected, memory_order_relaxed);
}

void lang_force_collect(void) { lang_collect_cycles(); }

/* ═══ Statistics ════════════════════════════════════════════════════ */

void arc_stats_get(ArcStats *out) {
    out->total_allocs      = atomic_load_explicit(&_stat_allocs,   memory_order_relaxed);
    out->total_frees       = atomic_load_explicit(&_stat_frees,    memory_order_relaxed);
    out->total_retains     = atomic_load_explicit(&_stat_retains,  memory_order_relaxed);
    out->total_releases    = atomic_load_explicit(&_stat_releases, memory_order_relaxed);
    out->cycles_collected  = atomic_load_explicit(&_stat_cycles,   memory_order_relaxed);
    out->objects_in_cycles = atomic_load_explicit(&_stat_cyc_obj,  memory_order_relaxed);
    out->live_objects      = (int64_t)(out->total_allocs - out->total_frees);
}

void arc_stats_print(void) {
    ArcStats s; arc_stats_get(&s);
    printf("ARC statistics\n");
    printf("  allocs          : %llu\n", (unsigned long long)s.total_allocs);
    printf("  frees           : %llu\n", (unsigned long long)s.total_frees);
    printf("  live objects    : %lld\n", (long long)s.live_objects);
    printf("  retains         : %llu\n", (unsigned long long)s.total_retains);
    printf("  releases        : %llu\n", (unsigned long long)s.total_releases);
    printf("  cycle passes    : %llu\n", (unsigned long long)s.cycles_collected);
    printf("  objects in cyc  : %llu\n", (unsigned long long)s.objects_in_cycles);
}

/* ═══════════════════════════════════════════════════════════════════
 * Generator runtime  (ucontext / swapcontext based)
 *
 * Each generator runs on its own private stack.  lang_gen_yield()
 * and lang_gen_resume() call swapcontext() to hand control back and
 * forth.  No LLVM coroutine machinery is involved.
 *
 * GenWrapper memory layout (must match codegen constants):
 *   offset  0-31  LangObj header
 *   offset 32     i8*   ctx       (pointer to LangGenCtx, GEN_CTX_IDX=5)
 *   offset 40     i64   yield_val (GEN_YIELD_IDX=6)
 *   offset 48     i64   send_val  (GEN_SEND_IDX=7)
 *   offset 56     i32   done      (GEN_DONE_IDX=8)
 *   offset 60     i32   _pad
 *   offset 64+    user params
 * ═══════════════════════════════════════════════════════════════════ */

#include <ucontext.h>

#define LANG_GEN_STACK_SIZE  (256 * 1024)   /* 256 KB per generator */

/* Accessors — struct offsets kept in sync with codegen constants */
#define _GEN_CTX_OFFSET   32
#define _GEN_YIELD_OFFSET 40
#define _GEN_SEND_OFFSET  48
#define _GEN_DONE_OFFSET  56

static inline void**    _g_ctx  (void *r){ return (void**)   ((char*)r+_GEN_CTX_OFFSET);   }
static inline int64_t*  _g_yval (void *r){ return (int64_t*) ((char*)r+_GEN_YIELD_OFFSET);  }
static inline int64_t*  _g_sval (void *r){ return (int64_t*) ((char*)r+_GEN_SEND_OFFSET);   }
static inline int32_t*  _g_done (void *r){ return (int32_t*) ((char*)r+_GEN_DONE_OFFSET);   }

typedef struct {
    ucontext_t  gen_ctx;
    ucontext_t  caller_ctx;
    char       *stack;
} LangGenCtx;

/* Trampoline args passed via pointer (lives on lang_gen_start's stack
 * until the first swapcontext returns, so the address is stable). */
typedef struct { void (*body)(void*); void *gen_raw; } _GenBoot;

static void _gen_trampoline(uint32_t hi, uint32_t lo) {
    /* Reconstruct 64-bit pointer from two 32-bit halves */
    uintptr_t ptr = ((uintptr_t)hi << 32) | (uintptr_t)(uint32_t)lo;
    _GenBoot  *b  = (_GenBoot*)ptr;
    void (*body)(void*) = b->body;
    void  *gen_raw      = b->gen_raw;
    /* Run the generator body */
    body(gen_raw);
    /* Generator finished naturally — mark done, return to caller */
    *_g_done(gen_raw) = 1;
    LangGenCtx *ctx = (LangGenCtx*)*_g_ctx(gen_raw);
    swapcontext(&ctx->gen_ctx, &ctx->caller_ctx);
    __builtin_unreachable();
}

/* Start a generator: allocate a private stack, set up the context,
 * and run the body until the first lang_gen_yield (or completion). */
void lang_gen_start(void *gen_raw, void (*body)(void*)) {
    LangGenCtx *ctx   = (LangGenCtx*)malloc(sizeof(LangGenCtx));
    char       *stack = (char*)malloc(LANG_GEN_STACK_SIZE);
    ctx->stack = stack;
    *_g_ctx(gen_raw) = ctx;

    getcontext(&ctx->gen_ctx);
    ctx->gen_ctx.uc_stack.ss_sp   = stack;
    ctx->gen_ctx.uc_stack.ss_size = LANG_GEN_STACK_SIZE;
    ctx->gen_ctx.uc_link          = NULL;   /* we handle termination manually */

    /* _GenBoot lives on OUR stack — valid until swapcontext returns below */
    _GenBoot bs = { body, gen_raw };
    uintptr_t p = (uintptr_t)&bs;
    makecontext(&ctx->gen_ctx, (void(*)())_gen_trampoline, 2,
                (uint32_t)(p >> 32), (uint32_t)p);

    /* Run generator to its first yield (or completion) */
    swapcontext(&ctx->caller_ctx, &ctx->gen_ctx);
}

/* Called from inside the generator body: stores the yielded value,
 * gives control back to the caller, and returns the sent-in value. */
int64_t lang_gen_yield(void *gen_raw, int64_t value) {
    LangGenCtx *ctx = (LangGenCtx*)*_g_ctx(gen_raw);
    *_g_yval(gen_raw) = value;
    swapcontext(&ctx->gen_ctx, &ctx->caller_ctx);
    return *_g_sval(gen_raw);
}

/* Called by the SendGen operator: resumes the generator and waits
 * until it yields again or finishes. */
void lang_gen_resume(void *gen_raw, int64_t send_val) {
    *_g_sval(gen_raw) = send_val;
    LangGenCtx *ctx = (LangGenCtx*)*_g_ctx(gen_raw);
    swapcontext(&ctx->caller_ctx, &ctx->gen_ctx);
}

/* Called by the dtor: free the private stack and context struct. */
void lang_gen_cleanup(void *gen_raw) {
    LangGenCtx *ctx = (LangGenCtx*)*_g_ctx(gen_raw);
    if (ctx) {
        free(ctx->stack);
        free(ctx);
        *_g_ctx(gen_raw) = NULL;
    }
}

/* ═══════════════════════════════════════════════════════════════════
 * String type  (ARC-managed, inline data)
 *
 * Layout:
 *   offset  0-31   LangObj header
 *   offset  32     i64  len
 *   offset  40     char data[len+1]   (null-terminated)
 * ═══════════════════════════════════════════════════════════════════ */

#include <string.h>
#include <stdio.h>
#include <stdarg.h>

/* Forward-declare vtable so lang_str_new can use it */
static void _lang_str_dtor(LangObj *obj);
static void _lang_str_visit(LangObj *obj, void(*cb)(LangObj*,void*), void *ctx);

static void _lang_str_dtor(LangObj *obj) {
    (void)obj;  /* inline data — base ARC free handles everything */
}

static void _lang_str_visit(LangObj *obj,
                             void (*cb)(LangObj *, void *), void *ctx) {
    (void)obj; (void)cb; (void)ctx;  /* no child objects */
}

/* String vtable — type_id=0x53545200 ("STR\0") */
static VTable lang_str_vtable = {
    .type_id       = 0x53545200u,
    .method_count  = 0,
    .destructor    = _lang_str_dtor,
    .visit_children= _lang_str_visit,
};

/* Accessors ─────────────────────────────────────────────────────── */

static inline int64_t* _str_len_ptr(void *obj) {
    return (int64_t*)((char*)obj + 32);
}
static inline char* _str_data_ptr(void *obj) {
    return (char*)obj + 40;
}

/* Public API ─────────────────────────────────────────────────────── */

/**
 * Create a new ARC string from a C string + length.
 * Copies `len` bytes from `cstr` and appends NUL.
 */
void* lang_str_new(const char *cstr, int64_t len) {
    /* Allocate: 40-byte header + len + 1 (NUL) */
    void *obj = lang_alloc(40 + len + 1, &lang_str_vtable);
    *_str_len_ptr(obj) = len;
    if (cstr && len > 0)
        memcpy(_str_data_ptr(obj), cstr, len);
    _str_data_ptr(obj)[len] = '\0';
    return obj;
}

/** Return the length of an ARC string. */
int64_t lang_str_len(void *obj) {
    if (!obj) return 0;
    return *_str_len_ptr(obj);
}

/** Return a pointer to the null-terminated char data. */
const char* lang_str_data(void *obj) {
    if (!obj) return "";
    return _str_data_ptr(obj);
}

/**
 * Safely convert any i8* to a C string for printf.
 * If obj is an ARC string (vtable matches), returns inline data.
 * Otherwise assumes it IS a raw C string and returns it unchanged.
 * Safe to call with NULL (returns "(null)").
 */
const char* lang_any_to_cstr(void *obj) {
    if (!obj) return "(null)";
    LangObj *lo = (LangObj*)obj;
    if (lo->vtable == &lang_str_vtable)
        return _str_data_ptr(obj);
    return (const char*)obj;   /* already a raw C string */
}

/**
 * Concatenate two ARC strings, return a new ARC string.
 * Either argument may be NULL (treated as empty).
 */
void* lang_str_concat(void *a, void *b) {
    int64_t la = a ? *_str_len_ptr(a) : 0;
    int64_t lb = b ? *_str_len_ptr(b) : 0;
    int64_t total = la + lb;
    void *obj = lang_alloc(40 + total + 1, &lang_str_vtable);
    *_str_len_ptr(obj) = total;
    if (a && la > 0) memcpy(_str_data_ptr(obj),      _str_data_ptr(a), la);
    if (b && lb > 0) memcpy(_str_data_ptr(obj) + la, _str_data_ptr(b), lb);
    _str_data_ptr(obj)[total] = '\0';
    return obj;
}

/**
 * Compare two ARC strings for equality.
 * Returns 1 if equal, 0 if not.
 */
int32_t lang_str_eq(void *a, void *b) {
    if (a == b) return 1;
    if (!a || !b) return 0;
    int64_t la = *_str_len_ptr(a);
    int64_t lb = *_str_len_ptr(b);
    if (la != lb) return 0;
    return memcmp(_str_data_ptr(a), _str_data_ptr(b), la) == 0 ? 1 : 0;
}

/**
 * Create an ARC string from a formatted C string.
 * Equivalent to: snprintf into a new ARC string.
 * Used internally by the fmt:"" builtin.
 */
void* lang_str_fmt(const char *fmt, ...) {
    va_list ap1, ap2;
    va_start(ap1, fmt);
    va_copy(ap2, ap1);
    int needed = vsnprintf(NULL, 0, fmt, ap1);
    va_end(ap1);
    if (needed < 0) { va_end(ap2); return lang_str_new("", 0); }
    void *obj = lang_alloc(40 + needed + 1, &lang_str_vtable);
    *_str_len_ptr(obj) = (int64_t)needed;
    vsnprintf(_str_data_ptr(obj), needed + 1, fmt, ap2);
    va_end(ap2);
    return obj;
}

/**
 * Create an ARC string from a null-terminated C string.
 * Convenience wrapper around lang_str_new.
 */
void* lang_str_from_cstr(const char *cstr) {
    if (!cstr) return lang_str_new("", 0);
    return lang_str_new(cstr, (int64_t)strlen(cstr));
}

/* ═══════════════════════════════════════════════════════════════════
 * Array type  (ARC-managed, resizable, i64 elements)
 *
 * Layout:
 *   offset  0-31   LangObj header
 *   offset  32     i64 len     (used elements)
 *   offset  40     i64 cap     (allocated slots)
 *   offset  48     i64* data   (heap buffer, NULL if cap==0)
 * ═══════════════════════════════════════════════════════════════════ */

static void _lang_arr_dtor(LangObj *obj) {
    int64_t *data = *(int64_t**)((char*)obj + 48);
    if (data) free(data);
}

static void _lang_arr_visit(LangObj *obj,
                             void (*cb)(LangObj *, void *), void *ctx) {
    (void)obj; (void)cb; (void)ctx;   /* int64 elements — no child objects */
}

static VTable lang_arr_vtable = {
    .type_id        = 0x41525200u,   /* "ARR\0" */
    .method_count   = 0,
    .destructor     = _lang_arr_dtor,
    .visit_children = _lang_arr_visit,
};

/* Accessors ─────────────────────────────────────────────────────── */
static inline int64_t*  _arr_len (void *o){ return (int64_t*) ((char*)o+32); }
static inline int64_t*  _arr_cap (void *o){ return (int64_t*) ((char*)o+40); }
static inline int64_t** _arr_data(void *o){ return (int64_t**)((char*)o+48); }

static void _arr_grow(void *obj, int64_t min_cap) {
    int64_t cap  = *_arr_cap(obj);
    int64_t ncap = cap < 4 ? 4 : cap * 2;
    if (ncap < min_cap) ncap = min_cap;
    int64_t *nd  = (int64_t*)realloc(*_arr_data(obj), ncap * sizeof(int64_t));
    *_arr_data(obj) = nd;
    *_arr_cap(obj)  = ncap;
}

/* Public API ─────────────────────────────────────────────────────── */

/** Create a new empty array with given initial capacity (0 = default). */
void* lang_arr_new(int64_t cap) {
    void *obj = lang_alloc(56, &lang_arr_vtable);  /* 32+8+8+8 */
    *_arr_len(obj) = 0;
    *_arr_cap(obj) = 0;
    *_arr_data(obj) = NULL;
    if (cap > 0) _arr_grow(obj, cap);
    return obj;
}

/** Append a value to the array (grows if needed). */
void lang_arr_push(void *obj, int64_t val) {
    int64_t len = *_arr_len(obj);
    if (len >= *_arr_cap(obj)) _arr_grow(obj, len + 1);
    (*_arr_data(obj))[len] = val;
    *_arr_len(obj) = len + 1;
}

/** Remove and return the last element (returns 0 if empty). */
int64_t lang_arr_pop(void *obj) {
    int64_t len = *_arr_len(obj);
    if (len == 0) return 0;
    *_arr_len(obj) = len - 1;
    return (*_arr_data(obj))[len - 1];
}

/** Get element at index (returns 0 if out of bounds). */
int64_t lang_arr_get(void *obj, int64_t idx) {
    int64_t len = *_arr_len(obj);
    if (idx < 0) idx = len + idx;          /* negative indexing */
    if (idx < 0 || idx >= len) return 0;
    return (*_arr_data(obj))[idx];
}

/** Set element at index (no-op if out of bounds). */
void lang_arr_set(void *obj, int64_t idx, int64_t val) {
    int64_t len = *_arr_len(obj);
    if (idx < 0) idx = len + idx;
    if (idx < 0 || idx >= len) return;
    (*_arr_data(obj))[idx] = val;
}

/** Return the number of elements. */
int64_t lang_arr_len(void *obj) {
    return obj ? *_arr_len(obj) : 0;
}

/**
 * Detect whether an i8* is an ARC array (by vtable).
 * Used by len: builtin to dispatch on type.
 */
int32_t lang_is_arr(void *obj) {
    if (!obj) return 0;
    return ((LangObj*)obj)->vtable == &lang_arr_vtable ? 1 : 0;
}

/* ═══════════════════════════════════════════════════════════════════
 * Standard Library  —  math, string ops, array extras, I/O, conversions
 * ═══════════════════════════════════════════════════════════════════ */

#include <sys/types.h>
#include <math.h>
#include <ctype.h>
#include <errno.h>

/* ── Math ─────────────────────────────────────────────────────────── */

int64_t lang_abs  (int64_t x)            { return x < 0 ? -x : x; }
int64_t lang_min  (int64_t a, int64_t b) { return a < b ? a : b; }
int64_t lang_max  (int64_t a, int64_t b) { return a > b ? a : b; }
int64_t lang_clamp(int64_t x, int64_t lo, int64_t hi)
                                         { return x < lo ? lo : x > hi ? hi : x; }
double  lang_fabs (double x)             { return fabs(x); }
double  lang_fmin (double a, double b)   { return fmin(a,b); }
double  lang_fmax (double a, double b)   { return fmax(a,b); }
double  lang_fclamp(double x, double lo, double hi)
                                         { return x < lo ? lo : x > hi ? hi : x; }
double  lang_sqrt (double x)             { return sqrt(x); }
double  lang_floor(double x)             { return floor(x); }
double  lang_ceil (double x)             { return ceil(x); }
double  lang_round(double x)             { return round(x); }
double  lang_sin  (double x)             { return sin(x); }
double  lang_cos  (double x)             { return cos(x); }
double  lang_tan  (double x)             { return tan(x); }
double  lang_log  (double x)             { return log(x); }
double  lang_log2 (double x)             { return log2(x); }
double  lang_log10(double x)             { return log10(x); }
double  lang_pow  (double x, double y)   { return pow(x,y); }

/* ── Type conversions ─────────────────────────────────────────────── */

void* lang_int_to_str(int64_t n) {
    char buf[32];
    int len = snprintf(buf, sizeof(buf), "%lld", (long long)n);
    return lang_str_new(buf, len);
}

void* lang_float_to_str(double f) {
    char buf[64];
    int len = snprintf(buf, sizeof(buf), "%g", f);
    return lang_str_new(buf, len);
}

void* lang_bool_to_str(int64_t b) {
    return b ? lang_str_new("true", 4) : lang_str_new("false", 5);
}

int64_t lang_str_to_int(void *str) {
    if (!str) return 0;
    const char *s = _str_data_ptr(str);
    errno = 0;
    char *end;
    int64_t result = (int64_t)strtoll(s, &end, 10);
    return (errno == 0 && end != s) ? result : 0;
}

double lang_str_to_float(void *str) {
    if (!str) return 0.0;
    const char *s = _str_data_ptr(str);
    char *end;
    double result = strtod(s, &end);
    return (end != s) ? result : 0.0;
}

/* ── String operations ────────────────────────────────────────────── */

void* lang_str_to_upper(void *str) {
    if (!str) return lang_str_new("", 0);
    int64_t len = *_str_len_ptr(str);
    void *out = lang_alloc(40 + len + 1, &lang_str_vtable);
    *_str_len_ptr(out) = len;
    const char *src = _str_data_ptr(str);
    char       *dst = _str_data_ptr(out);
    for (int64_t i = 0; i < len; i++) dst[i] = (char)toupper((unsigned char)src[i]);
    dst[len] = '\0';
    return out;
}

void* lang_str_to_lower(void *str) {
    if (!str) return lang_str_new("", 0);
    int64_t len = *_str_len_ptr(str);
    void *out = lang_alloc(40 + len + 1, &lang_str_vtable);
    *_str_len_ptr(out) = len;
    const char *src = _str_data_ptr(str);
    char       *dst = _str_data_ptr(out);
    for (int64_t i = 0; i < len; i++) dst[i] = (char)tolower((unsigned char)src[i]);
    dst[len] = '\0';
    return out;
}

void* lang_str_trim(void *str) {
    if (!str) return lang_str_new("", 0);
    const char *s = _str_data_ptr(str);
    int64_t len   = *_str_len_ptr(str);
    int64_t start = 0, end = len;
    while (start < end && isspace((unsigned char)s[start])) start++;
    while (end > start && isspace((unsigned char)s[end-1])) end--;
    return lang_str_new(s + start, end - start);
}

void* lang_str_trim_start(void *str) {
    if (!str) return lang_str_new("", 0);
    const char *s = _str_data_ptr(str);
    int64_t len = *_str_len_ptr(str), i = 0;
    while (i < len && isspace((unsigned char)s[i])) i++;
    return lang_str_new(s + i, len - i);
}

void* lang_str_trim_end(void *str) {
    if (!str) return lang_str_new("", 0);
    const char *s = _str_data_ptr(str);
    int64_t end = *_str_len_ptr(str);
    while (end > 0 && isspace((unsigned char)s[end-1])) end--;
    return lang_str_new(s, end);
}

int32_t lang_str_contains(void *str, void *sub) {
    if (!str || !sub) return 0;
    return strstr(_str_data_ptr(str), _str_data_ptr(sub)) != NULL ? 1 : 0;
}

int32_t lang_str_starts_with(void *str, void *prefix) {
    if (!str || !prefix) return 0;
    int64_t plen = *_str_len_ptr(prefix);
    if (*_str_len_ptr(str) < plen) return 0;
    return memcmp(_str_data_ptr(str), _str_data_ptr(prefix), plen) == 0 ? 1 : 0;
}

int32_t lang_str_ends_with(void *str, void *suffix) {
    if (!str || !suffix) return 0;
    int64_t slen = *_str_len_ptr(str), sflen = *_str_len_ptr(suffix);
    if (slen < sflen) return 0;
    return memcmp(_str_data_ptr(str) + slen - sflen,
                  _str_data_ptr(suffix), sflen) == 0 ? 1 : 0;
}

int64_t lang_str_index_of(void *str, void *sub) {
    if (!str || !sub) return -1;
    const char *found = strstr(_str_data_ptr(str), _str_data_ptr(sub));
    return found ? (int64_t)(found - _str_data_ptr(str)) : -1;
}

void* lang_str_slice(void *str, int64_t start, int64_t end) {
    if (!str) return lang_str_new("", 0);
    int64_t len = *_str_len_ptr(str);
    if (start < 0) start = len + start;
    if (end   < 0) end   = len + end;
    if (start < 0) start = 0;
    if (end > len) end = len;
    if (start >= end) return lang_str_new("", 0);
    return lang_str_new(_str_data_ptr(str) + start, end - start);
}

void* lang_str_replace(void *str, void *from, void *to) {
    if (!str || !from) return str ? lang_retain(str) : lang_str_new("", 0);
    const char *s    = _str_data_ptr(str);
    const char *f    = _str_data_ptr(from);
    const char *t    = to ? _str_data_ptr(to) : "";
    int64_t flen = *_str_len_ptr(from);
    int64_t tlen = to ? *_str_len_ptr(to) : 0;
    if (flen == 0) return lang_str_from_cstr(s);

    /* Count occurrences to size the output buffer */
    int64_t count = 0;
    const char *p = s;
    while ((p = strstr(p, f)) != NULL) { count++; p += flen; }
    if (count == 0) return lang_str_from_cstr(s);

    int64_t olen = *_str_len_ptr(str) + count * (tlen - flen);
    void *out = lang_alloc(40 + olen + 1, &lang_str_vtable);
    *_str_len_ptr(out) = olen;
    char *dst = _str_data_ptr(out);
    p = s;
    while (*p) {
        const char *found = strstr(p, f);
        if (!found) { strcpy(dst, p); break; }
        memcpy(dst, p, found - p); dst += found - p;
        memcpy(dst, t, tlen);      dst += tlen;
        p = found + flen;
    }
    *dst = '\0';
    return out;
}

void* lang_str_repeat(void *str, int64_t n) {
    if (!str || n <= 0) return lang_str_new("", 0);
    int64_t slen = *_str_len_ptr(str);
    int64_t olen = slen * n;
    void *out = lang_alloc(40 + olen + 1, &lang_str_vtable);
    *_str_len_ptr(out) = olen;
    char *dst = _str_data_ptr(out);
    for (int64_t i = 0; i < n; i++) memcpy(dst + i*slen, _str_data_ptr(str), slen);
    dst[olen] = '\0';
    return out;
}

/* ── Array extras ─────────────────────────────────────────────────── */

static int _cmp_i64_asc(const void *a, const void *b) {
    int64_t ia = *(const int64_t*)a, ib = *(const int64_t*)b;
    return (ia > ib) - (ia < ib);
}

void lang_arr_sort(void *obj) {
    if (!obj) return;
    int64_t len = *_arr_len(obj);
    if (len > 1) qsort(*_arr_data(obj), len, sizeof(int64_t), _cmp_i64_asc);
}

void lang_arr_reverse(void *obj) {
    if (!obj) return;
    int64_t len = *_arr_len(obj), *d = *_arr_data(obj);
    for (int64_t i = 0, j = len-1; i < j; i++, j--) {
        int64_t tmp = d[i]; d[i] = d[j]; d[j] = tmp;
    }
}

void* lang_arr_slice(void *obj, int64_t start, int64_t end) {
    if (!obj) return lang_arr_new(0);
    int64_t len = *_arr_len(obj);
    if (start < 0) start = len + start;
    if (end   < 0) end   = len + end;
    if (start < 0) start = 0;
    if (end > len) end = len;
    if (start >= end) return lang_arr_new(0);
    int64_t nlen = end - start;
    void *out = lang_arr_new(nlen);
    int64_t *src = *_arr_data(obj) + start;
    for (int64_t i = 0; i < nlen; i++) lang_arr_push(out, src[i]);
    return out;
}

int32_t lang_arr_contains(void *obj, int64_t val) {
    if (!obj) return 0;
    int64_t len = *_arr_len(obj), *d = *_arr_data(obj);
    for (int64_t i = 0; i < len; i++) if (d[i] == val) return 1;
    return 0;
}

int64_t lang_arr_index_of(void *obj, int64_t val) {
    if (!obj) return -1;
    int64_t len = *_arr_len(obj), *d = *_arr_data(obj);
    for (int64_t i = 0; i < len; i++) if (d[i] == val) return i;
    return -1;
}

void* lang_arr_concat(void *a, void *b) {
    int64_t alen = a ? *_arr_len(a) : 0;
    int64_t blen = b ? *_arr_len(b) : 0;
    void *out = lang_arr_new(alen + blen);
    for (int64_t i = 0; i < alen; i++) lang_arr_push(out, (*_arr_data(a))[i]);
    for (int64_t i = 0; i < blen; i++) lang_arr_push(out, (*_arr_data(b))[i]);
    return out;
}

/* ── I/O ──────────────────────────────────────────────────────────── */

void* lang_read_line(void) {
    char *line = NULL;
    size_t cap = 0;
    ssize_t len = getline(&line, &cap, stdin);
    if (len < 0) { free(line); return lang_str_new("", 0); }
    /* Strip trailing newline */
    if (len > 0 && line[len-1] == '\n') len--;
    void *s = lang_str_new(line, (int64_t)len);
    free(line);
    return s;
}

void* lang_read_file(void *path) {
    if (!path) return lang_str_new("", 0);
    FILE *f = fopen(_str_data_ptr(path), "rb");
    if (!f) return lang_str_new("", 0);
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    rewind(f);
    void *out = lang_alloc(40 + size + 1, &lang_str_vtable);
    *_str_len_ptr(out) = size;
    (void)fread(_str_data_ptr(out), 1, size, f);
    _str_data_ptr(out)[size] = '\0';
    fclose(f);
    return out;
}

int32_t lang_write_file(void *path, void *content) {
    if (!path || !content) return 0;
    FILE *f = fopen(_str_data_ptr(path), "wb");
    if (!f) return 0;
    int64_t len = *_str_len_ptr(content);
    fwrite(_str_data_ptr(content), 1, len, f);
    fclose(f);
    return 1;
}

int32_t lang_append_file(void *path, void *content) {
    if (!path || !content) return 0;
    FILE *f = fopen(_str_data_ptr(path), "ab");
    if (!f) return 0;
    int64_t len = *_str_len_ptr(content);
    fwrite(_str_data_ptr(content), 1, len, f);
    fclose(f);
    return 1;
}

int32_t lang_file_exists(void *path) {
    if (!path) return 0;
    FILE *f = fopen(_str_data_ptr(path), "rb");
    if (!f) return 0;
    fclose(f);
    return 1;
}

void lang_print_err(void *str) {
    if (!str) return;
    fprintf(stderr, "%s\n", _str_data_ptr(str));
}
