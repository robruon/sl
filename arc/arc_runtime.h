/*
 * arc_runtime.h  ·  Automatic Reference Counting runtime
 * ═══════════════════════════════════════════════════════
 *
 * Memory model
 * ────────────
 * Every heap object begins with LangObj (the "header").
 * The codegen guarantees this layout for every class.
 *
 *   ┌──────────────┬──────────────┬──────────────┬──────────────┬─────────┐
 *   │  vtable ptr  │  refcount    │  weak_count  │  color       │  dtor   │
 *   │  (8 bytes)   │  (atomic i64)│  (atomic i64)│  (atomic i32)│  (ptr)  │
 *   └──────────────┴──────────────┴──────────────┴──────────────┴─────────┘
 *   │◄──────────────────── sizeof(LangObj) = 40 bytes ───────────────────►│
 *   └── class-specific fields follow immediately ──────────────────────────
 *
 * Strong refcount
 *   Starts at 1 (the allocator's reference).
 *   lang_retain  increments  (memory_order_relaxed).
 *   lang_release decrements  (memory_order_acq_rel).
 *   Reaches 0 → destructor is called, then weak_count is decremented.
 *
 * Weak count
 *   Starts at 1 (representing "the strong side").
 *   Every WeakRef increments it.
 *   When refcount → 0, the strong side decrements weak_count.
 *   When weak_count → 0, memory is freed.
 *   This ensures memory stays valid as long as any weak ref exists,
 *   even after the object is logically dead.
 *
 * Color  (cycle detector)
 *   BLACK  = 0  in use, not suspected
 *   GRAY   = 1  currently being visited (trial deletion in progress)
 *   WHITE  = 2  confirmed garbage after trial deletion
 *   PURPLE = 3  candidate for cycle detection (added to purple set)
 */

#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif


/* ── Object color tags for Bacon-Rajan cycle collector ──────────── */
#define ARC_BLACK   0
#define ARC_GRAY    1
#define ARC_WHITE   2
#define ARC_PURPLE  3
#define ARC_DEAD    4  /* in _white_buf, awaiting bulk-free this GC pass */


/* ── VTable layout ──────────────────────────────────────────────────
 *
 * Every class has a statically allocated vtable of this form.
 * The codegen fills it at compile time.
 *
 * vtable[0]  →  destructor  (called when refcount reaches 0)
 * vtable[1]  →  visit_children  (called by cycle detector)
 * vtable[2+] →  virtual method pointers (in declaration order)
 */
typedef struct LangObj LangObj;

typedef void (*DtorFn)   (LangObj *self);
typedef void (*VisitFn)  (LangObj *self, void (*cb)(LangObj *child, void *ctx), void *ctx);
typedef void (*MethodFn) (void);   /* generic slot — cast at call site */

typedef struct {
    uint32_t   type_id;           /* unique type identifier (hash of name) */
    uint32_t   method_count;      /* number of method slots that follow      */
    DtorFn     destructor;        /* vtable[0]  — mandatory                  */
    VisitFn    visit_children;    /* vtable[1]  — NULL if no object fields   */
    MethodFn   methods[];         /* vtable[2+] — FAM, cast at each call site*/
} VTable;


/* ── Object header ──────────────────────────────────────────────── */
struct LangObj {
    VTable          *vtable;      /* dispatch table (set by init)            */
    _Atomic int64_t  refcount;    /* strong ARC count (starts at 1)          */
    _Atomic int64_t  weak_count;  /* weak refs + 1 while any strong ref live */
    _Atomic int32_t  color;       /* cycle-detector color                    */
    int32_t          _pad;        /* alignment padding                       */
};
/* Static assert so codegen can hard-code the field offsets */
_Static_assert(sizeof(LangObj) == 32, "LangObj must be 32 bytes");
_Static_assert(_Alignof(LangObj) == 8, "LangObj must be 8-byte aligned");


/* ── Weak reference ─────────────────────────────────────────────── */
typedef struct {
    _Atomic(LangObj *) target;    /* NULL once the object has been freed     */
} WeakRef;


/* ═══════════════════════════════════════════════════════════════════
 * Public API
 * ═══════════════════════════════════════════════════════════════════ */

/* ── Allocation ─────────────────────────────────────────────────── */

/**
 * Allocate `size` bytes, zero-initialised.  Never returns NULL.
 * Initialises all ARC header fields (refcount=1, weak_count=1, color=BLACK).
 * `size` must be >= sizeof(LangObj).
 */
LangObj *lang_alloc(size_t size, VTable *vtable);

/**
 * Allocate and copy `size` bytes from `src` (for string/buffer objects).
 * ARC header fields are initialised the same way as lang_alloc.
 */
LangObj *lang_alloc_copy(size_t size, VTable *vtable, const void *src);


/* ── Retain / Release ───────────────────────────────────────────── */

/**
 * Increment the strong refcount.
 * Thread-safe.  Returns `obj` for chaining: x = lang_retain(make_obj()).
 */
LangObj *lang_retain(LangObj *obj);

/**
 * Decrement the strong refcount.
 * When it reaches 0: destructor is called, weak_count is decremented.
 * If this causes weak_count to also reach 0, memory is freed immediately.
 */
void lang_release(LangObj *obj);

/** Retain only if obj is non-null (safe for optional / nullable fields). */
static inline LangObj *lang_retain_opt(LangObj *obj) {
    return obj ? lang_retain(obj) : NULL;
}

/** Release only if obj is non-null. */
static inline void lang_release_opt(LangObj *obj) {
    if (obj) lang_release(obj);
}


/* ── Weak references ────────────────────────────────────────────── */

/**
 * Create a weak reference to `obj`.
 * Does NOT retain obj.  Increments obj's weak_count.
 * Returns a heap-allocated WeakRef — caller owns it.
 */
WeakRef *lang_weak_ref(LangObj *obj);

/**
 * Attempt to promote a WeakRef to a strong reference.
 *
 * Uses a CAS loop to atomically increment the strong refcount from > 0.
 * Returns a retained LangObj* (caller must lang_release it when done).
 * Returns NULL if the object has already been deallocated.
 *
 * This is the "swift_tryRetain" pattern.
 */
LangObj *lang_weak_deref(const WeakRef *ref);

/**
 * Release a WeakRef itself (not the object it points to).
 * Decrements the target's weak_count; frees memory if both counts reach 0.
 * Frees the WeakRef struct.
 */
void lang_weak_release(WeakRef *ref);


/* ── Cycle collector (Bacon-Rajan 2001) ─────────────────────────── */

/**
 * Add `obj` to the purple (candidate) set.
 * Called internally by lang_release when refcount drops to > 0
 * (i.e. the object might be part of a reference cycle).
 */
void lang_cycle_candidate(LangObj *obj);

/**
 * Run the three-phase Bacon-Rajan cycle collector over all candidates.
 *
 * Phase 1 (Mark gray):   trial-delete by decrementing children's refcounts.
 * Phase 2 (Scan):        restore live objects (refcount still > 0 after trial).
 * Phase 3 (Collect):     free confirmed garbage (refcount == 0 after trial).
 *
 * This is a stop-the-world pass in this implementation.
 * Production: run from a background thread with a read-write lock.
 */
void lang_collect_cycles(void);

/**
 * Force a full collection right now (useful for tests and shutdown).
 */
void lang_force_collect(void);


/* ── Runtime statistics (debug / profiling) ─────────────────────── */

typedef struct {
    uint64_t total_allocs;
    uint64_t total_frees;
    uint64_t total_retains;
    uint64_t total_releases;
    uint64_t cycles_collected;
    uint64_t objects_in_cycles;
    int64_t  live_objects;       /* allocs − frees */
} ArcStats;

void     arc_stats_get(ArcStats *out);
void     arc_stats_print(void);

#ifdef __cplusplus
}
#endif

/* ── Generator runtime (ucontext-based) ─────────────────────────── */

/**
 * Initialise and start a generator.
 * Allocates a private stack, sets up gen_raw's ctx field, then runs
 * body(gen_raw) until the first lang_gen_yield() or completion.
 * After return, gen_raw->yield_val holds the first yielded value and
 * gen_raw->done is 1 if the generator finished without yielding.
 */
void    lang_gen_start(void *gen_raw, void (*body)(void*));

/**
 * Called from inside the generator body to yield a value.
 * Stores value, suspends the generator, and returns the send value
 * that the caller provided via lang_gen_resume().
 */
int64_t lang_gen_yield(void *gen_raw, int64_t value);

/**
 * Called by <<| (SendGen): resume the suspended generator.
 * Stores send_val, wakes the generator, and returns when the
 * generator yields again or finishes.
 */
void    lang_gen_resume(void *gen_raw, int64_t send_val);

/**
 * Called by the generator wrapper's ARC destructor.
 * Frees the private stack and context struct.
 */
void    lang_gen_cleanup(void *gen_raw);

/* ── String type ──────────────────────────────────────────────────── */

/** Create an ARC string from a C string and length. */
void*       lang_str_new(const char *cstr, int64_t len);

/** Create an ARC string from a null-terminated C string. */
void*       lang_str_from_cstr(const char *cstr);

/** Return the length of an ARC string. */
int64_t     lang_str_len(void *str);

/** Return a pointer to the string's null-terminated char data. */
const char* lang_str_data(void *str);

/**
 * Safely convert any i8* to a C string for printf.
 * Detects ARC strings by vtable and returns inline data;
 * otherwise returns the pointer unchanged (raw C string).
 */
const char* lang_any_to_cstr(void *val);

/** Concatenate two ARC strings, return a new ARC string. */
void*       lang_str_concat(void *a, void *b);

/** Compare two ARC strings. Returns 1 if equal, 0 if not. */
int32_t     lang_str_eq(void *a, void *b);

/** Printf-style format into a new ARC string. */
void*       lang_str_fmt(const char *fmt, ...);

/* ── Array type (i64 elements, ARC-managed) ──────────────────────── */

/** Create a new empty array. cap=0 uses a default initial capacity. */
void*   lang_arr_new(int64_t cap);

/** Append a value to the end of the array. */
void    lang_arr_push(void *arr, int64_t val);

/** Remove and return the last element (0 if empty). */
int64_t lang_arr_pop(void *arr);

/** Get element at index. Supports negative indexing. 0 if out of bounds. */
int64_t lang_arr_get(void *arr, int64_t idx);

/** Set element at index. No-op if out of bounds. */
void    lang_arr_set(void *arr, int64_t idx, int64_t val);

/** Return the number of elements. */
int64_t lang_arr_len(void *arr);

/** Returns 1 if obj is an ARC array, 0 otherwise. */
int32_t lang_is_arr(void *arr);
