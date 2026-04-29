# -*- coding: utf-8 -*-
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 10):
    _sys.exit("Error: SL requires Python 3.10+. Running: " + _sys.version)
del _sys
"""
codegen.py  ·  LLVM IR codegen for the symbolic language
─────────────────────────────────────────────────────────
Walks the AST produced by parser.py and emits LLVM IR via llvmlite.

Type strategy
─────────────
  int, float, bool  →  i64 / double / i1   (scalars, no ARC)
  everything else   →  i8*  (opaque pointer to LangObj header, ARC-managed)

Object / class layout
──────────────────────
  Every class struct begins with the LangObj header (5 fields),
  then the user-declared fields follow immediately.

    { i8*, i64, i64, i32, i32,  <field0>, <field1>, ... }
      vt    rc   wc  clr  pad   ← LangObj header (indices 0-4)

  A vtable global is emitted for each class:
    { i32, i32, i8*, i8*, i8*, ... }
      tid  cnt  dtor vis  method0 method1 ...
  (indices 0-1 = type_id / method_count, 2 = dtor, 3 = visitor, 4+ = methods)

Usage
─────
  tree = Parser(lex(src), 'mymod').parse()
  cg   = Codegen('mymod')
  cg.visit(tree)
  print(cg.module)
  cg.compile_to_object('output.o')

CLI
───
  python codegen.py source.sl          # print IR
  python codegen.py source.sl -o a.o   # compile to object
  python codegen.py source.sl --run    # JIT + execute main()
"""


import hashlib, sys
from dataclasses import dataclass, field as dc_field
from typing import Any, Optional

from llvmlite import ir, binding

binding.initialize_native_target()
binding.initialize_native_asmprinter()

from lexer  import lex
from parser import (
    Parser, NodeVisitor, pretty,
    TyName, TyList, TyDict, TyTuple, TyOptional, TyUnion, TyGen, TyFn, TyParam,
    IntLit, FloatLit, StrLit, BoolLit, NullLit, Ident, SelfExpr, ClassRef,
    BinOp, UnaryOp, Assign, AugAssign,
    PropAccess, SafePropAccess, MethodCall, SafeMethodCall,
    Call, SelfCall, Pipe, NullCoalesce, Ternary,
    RangeExpr, YieldExpr, YieldFromExpr, AwaitExpr, GatherExpr,
    FireForget, RecvChan, SendGen, ThrowGen, WeakRefExpr,
    PartialApp, Lambda, ListLit, DictLit, SetLit, TupleLit,
    Comprehension, InterpolatedStr, Subscript,
    NotNullExpr, LenExpr, IterExpr,
    Param, Block, FnDef, ClassDef, MixinDef, IfaceDef,
    ReturnStmt, YieldStmt, YieldFromStmt,
    IfStmt, WhileStmt, ForStmt, BreakStmt, ContinueStmt,
    ExprStmt, ImportStmt, TryCatch, AssertStmt,
    MixinAttach, Namespace, Program, ExternC,
)


# ── LLVM type singletons ─────────────────────────────────────────────

i1   = ir.IntType(1)
i8   = ir.IntType(8)
i32  = ir.IntType(32)
i64  = ir.IntType(64)
dbl  = ir.DoubleType()
void = ir.VoidType()
i8p  = ir.PointerType(i8)   # opaque object / char pointer

# LangObj header — must match arc_runtime.h
#   field 0: i8*  vtable ptr
#   field 1: i64  refcount
#   field 2: i64  weak_count
#   field 3: i32  color
#   field 4: i32  pad
HEADER_FIELDS = [i8p, i64, i64, i32, i32]
HEADER_T      = ir.LiteralStructType(HEADER_FIELDS)
N_HEADER      = len(HEADER_FIELDS)   # 5

# Vtable layout: [type_id i32, method_count i32, dtor i8*, visitor i8*, methods i8*...]
# All function ptrs stored as i8* and bitcast at call sites.

NULL  = ir.Constant(i8p, None)
TRUE  = ir.Constant(i1, 1)
FALSE = ir.Constant(i1, 0)


# ── Scope / symbol table ─────────────────────────────────────────────

@dataclass
class VarSlot:
    ptr:      ir.Value            # alloca ptr
    ty:       ir.Type             # value type stored in slot
    is_obj:   bool                # True → ARC-managed i8*
    gen_name: Optional[str] = None  # non-None when slot holds a generator wrapper

class Scope:
    def __init__(self):
        self._vars: dict[str, VarSlot] = {}

    def define(self, name: str, slot: VarSlot):
        self._vars[name] = slot

    def lookup(self, name: str) -> Optional[VarSlot]:
        return self._vars.get(name)

    def all_obj_slots(self) -> list[VarSlot]:
        return [s for s in self._vars.values() if s.is_obj]


# ── Class metadata ───────────────────────────────────────────────────

@dataclass
class ClassInfo:
    name:        str
    struct_ty:   ir.LiteralStructType    # full struct (header + user fields)
    vtable_ty:   ir.LiteralStructType    # vtable struct type
    vtable_gv:   ir.GlobalVariable       # global vtable constant
    type_id:     int                     # hash of class name
    # field metadata (user fields only, indices relative to N_HEADER)
    field_names: list[str]               = dc_field(default_factory=list)
    field_index: dict[str, int]          = dc_field(default_factory=dict)
    field_type:  dict[str, ir.Type]      = dc_field(default_factory=dict)
    field_is_obj:dict[str, bool]         = dc_field(default_factory=dict)
    # method metadata
    method_names:list[str]               = dc_field(default_factory=list)
    method_slot: dict[str, int]          = dc_field(default_factory=dict)
    method_fnty: dict[str, ir.FunctionType] = dc_field(default_factory=dict)
    method_fn:   dict[str, ir.Function]  = dc_field(default_factory=dict)
    # generated functions
    constructor: Optional[ir.Function]  = None
    dtor:        Optional[ir.Function]  = None
    visitor:     Optional[ir.Function]  = None

    def struct_field_index(self, field_name: str) -> int:
        """Absolute index into struct_ty for a user field."""
        return N_HEADER + self.field_index[field_name]


# ── Generator wrapper metadata ──────────────────────────────────────
#
# Every generator type gets a GenWrapper:
#   { LangObj header (5 fields), coro_hdl i8*, yield_val T, send_val T,
#     done i32, pad i32 }
#   indices:  0-4 (header)   5         6           7         8     9
#
# The wrapper is ARC-managed.  Its destructor calls coro.destroy.
# The "ramp" function (presplitcoroutine) takes the wrapper as its first
# argument so LLVM's CoroSplitPass can access the frame through it.

# GenWrapper field indices (in the LLVM LiteralStructType)
GEN_CTX_IDX   = 5   # i8*  ctx pointer (→ LangGenCtx with ucontext pair)
GEN_YIELD_IDX = 6   # i64  yield_val   (written by body before each yield)
GEN_SEND_IDX  = 7   # i64  send_val    (written by SendGen before each resume)
GEN_DONE_IDX  = 8   # i32  done flag   (set to 1 by trampoline when body returns)

# Byte offset of user params inside the GenWrapper allocation.
# Layout: 32 (LangObj hdr) + 8 (ctx) + 8 (yield) + 8 (send) + 4 (done) + 4 (pad) = 64
GEN_PARAMS_OFFSET = 64

@dataclass
class GenInfo:
    name:       str
    yield_ty:   ir.Type              # LLVM type of yielded values
    send_ty:    ir.Type              # LLVM type of sent values (mirrors yield_ty)
    wrapper_ty: ir.LiteralStructType # fixed 64-byte header (no user fields)
    vtable_ty:  ir.LiteralStructType
    vtable_gv:  ir.GlobalVariable
    param_tys:  list                 # LLVM types of user params (in order)
    init_fn:    Optional[ir.Function] = None   # allocates wrapper, starts body
    body_fn:    Optional[ir.Function] = None   # void body(i8* gen_raw)
    dtor_fn:    Optional[ir.Function] = None   # calls lang_gen_cleanup


class CodegenError(Exception):
    def __init__(self, msg: str, node=None):
        loc = f" (line {node.line}:{node.col})" if node and hasattr(node, 'line') else ""
        super().__init__(f"CodegenError{loc}: {msg}")


# ════════════════════════════════════════════════════════════════════
# Codegen visitor
# ════════════════════════════════════════════════════════════════════

class Codegen(NodeVisitor):

    def __init__(self, module_name: str = "lang_module",
                 module=None, source_file: str = None):
        self._source_file  = source_file or (
            module_name if module_name.endswith(('.sl','.slb')) else None)
        if module is not None:
            # Sub-compilation sharing an existing module
            self.module = module
        else:
            self.module        = ir.Module(name=module_name)
            self.module.triple = binding.get_default_triple()

        # Per-function state
        self._builder:       Optional[ir.IRBuilder] = None
        self._fn:            Optional[ir.Function]  = None
        self._fn_node:       Optional[FnDef]        = None
        self._alloca_builder:Optional[ir.IRBuilder] = None
        self._alloca_blk:    Optional[ir.Block]     = None
        self._current_class: Optional[ClassInfo]    = None
        self._current_gen:      Optional[GenInfo]  = None
        self._gen_wrapper_slot: Optional[ir.Value] = None
        # Namespace tracking
        self._current_ns:  Optional[str]        = None   # set inside ~[name] blocks
        self._namespaces:  dict[str, dict]       = {}     # name → {symbol→VarSlot/FnInfo}
        self._ns_modules:  dict[str, 'Codegen']  = {}     # name → compiled Codegen
        # (no coro hdl/cleanup state — using ucontext runtime instead)

        # Class and generator registries
        self._classes:  dict[str, ClassInfo] = {}
        self._gens:     dict[str, GenInfo]   = {}

        # Scope and loop stacks
        self._scopes:    list[Scope]                    = []
        self._loop_stack:list[tuple[ir.Block,ir.Block]] = []

        # String literal cache
        self._str_cache: dict[str, ir.GlobalVariable] = {}

        self._declare_runtime()
        self._declare_builtins()

    # ── Runtime declarations ─────────────────────────────────────────

    def _declare_runtime(self):
        def _fn(name, ret, *args):
            if name in self.module.globals:
                return self.module.globals[name]
            ft = ir.FunctionType(ret, list(args))
            f  = ir.Function(self.module, ft, name=name)
            f.linkage = 'external'
            return f

        self._fn_retain      = _fn('lang_retain',        i8p,  i8p)
        self._fn_release     = _fn('lang_release',       void, i8p)
        self._fn_alloc       = _fn('lang_alloc',         i8p,  i64, i8p)
        self._fn_collect     = _fn('lang_force_collect', void)
        self._fn_malloc      = _fn('malloc',             i8p,  i64)
        self._fn_free        = _fn('free',               void, i8p)
        self._fn_weak_ref    = _fn('lang_weak_ref',      i8p,  i8p)
        self._fn_weak_deref  = _fn('lang_weak_deref',    i8p,  i8p)
        self._fn_weak_release= _fn('lang_weak_release',  void, i8p)
        def _vfn(name, ret, extra_args=None, var_arg=False):
            if name in self.module.globals:
                return self.module.globals[name]
            args = extra_args or []
            ft = ir.FunctionType(ret, args, var_arg=var_arg)
            f  = ir.Function(self.module, ft, name=name)
            f.linkage = 'external'; return f
        self._fn_printf   = _vfn('printf',   i32, [i8p], var_arg=True)
        self._fn_snprintf = _vfn('snprintf', i32, [i8p, i64, i8p], var_arg=True)
        # String runtime
        def _sfn(name, ret, *args):
            if name in self.module.globals:
                return self.module.globals[name]
            f = ir.Function(self.module, ir.FunctionType(ret, list(args)), name=name)
            f.linkage = 'external'; return f
        self._fn_str_new      = _sfn('lang_str_new',      i8p, i8p, i64)
        self._fn_str_from_cstr= _sfn('lang_str_from_cstr',i8p, i8p)
        self._fn_str_len      = _sfn('lang_str_len',      i64, i8p)
        self._fn_str_data     = _sfn('lang_str_data',     i8p, i8p)
        self._fn_any_to_cstr  = _sfn('lang_any_to_cstr',  i8p, i8p)
        self._fn_str_concat   = _sfn('lang_str_concat',   i8p, i8p, i8p)
        self._fn_str_eq       = _sfn('lang_str_eq',       i32, i8p, i8p)
        # Array runtime
        self._fn_arr_new     = _sfn('lang_arr_new',     i8p, i64)
        self._fn_arr_push    = _sfn('lang_arr_push',    void, i8p, i64)
        self._fn_arr_pop     = _sfn('lang_arr_pop',     i64, i8p)
        self._fn_arr_get     = _sfn('lang_arr_get',     i64, i8p, i64)
        self._fn_arr_set     = _sfn('lang_arr_set',     void, i8p, i64, i64)
        self._fn_arr_len     = _sfn('lang_arr_len',     i64, i8p)
        self._fn_is_arr      = _sfn('lang_is_arr',      i32, i8p)
        self._fn_arr_sort    = _sfn('lang_arr_sort',    void, i8p)
        self._fn_arr_reverse = _sfn('lang_arr_reverse', void, i8p)
        self._fn_arr_slice   = _sfn('lang_arr_slice',   i8p, i8p, i64, i64)
        self._fn_arr_contains= _sfn('lang_arr_contains',i32, i8p, i64)
        self._fn_arr_indexof = _sfn('lang_arr_index_of',i64, i8p, i64)
        self._fn_arr_concat  = _sfn('lang_arr_concat',  i8p, i8p, i8p)
        # Math
        self._fn_abs    = _sfn('lang_abs',    i64, i64)
        self._fn_min    = _sfn('lang_min',    i64, i64, i64)
        self._fn_max    = _sfn('lang_max',    i64, i64, i64)
        self._fn_clamp  = _sfn('lang_clamp',  i64, i64, i64, i64)
        self._fn_fabs   = _sfn('lang_fabs',   dbl, dbl)
        self._fn_fmin   = _sfn('lang_fmin',   dbl, dbl, dbl)
        self._fn_fmax   = _sfn('lang_fmax',   dbl, dbl, dbl)
        self._fn_fclamp = _sfn('lang_fclamp', dbl, dbl, dbl, dbl)
        self._fn_sqrt   = _sfn('lang_sqrt',   dbl, dbl)
        self._fn_floor  = _sfn('lang_floor',  dbl, dbl)
        self._fn_ceil   = _sfn('lang_ceil',   dbl, dbl)
        self._fn_round  = _sfn('lang_round',  dbl, dbl)
        self._fn_sin    = _sfn('lang_sin',    dbl, dbl)
        self._fn_cos    = _sfn('lang_cos',    dbl, dbl)
        self._fn_tan    = _sfn('lang_tan',    dbl, dbl)
        self._fn_log    = _sfn('lang_log',    dbl, dbl)
        self._fn_log2   = _sfn('lang_log2',   dbl, dbl)
        self._fn_log10  = _sfn('lang_log10',  dbl, dbl)
        self._fn_pow    = _sfn('lang_pow',    dbl, dbl, dbl)
        # Type conversions
        self._fn_int_to_str    = _sfn('lang_int_to_str',   i8p, i64)
        self._fn_float_to_str  = _sfn('lang_float_to_str', i8p, dbl)
        self._fn_bool_to_str   = _sfn('lang_bool_to_str',  i8p, i64)
        self._fn_str_to_int    = _sfn('lang_str_to_int',   i64, i8p)
        self._fn_str_to_float  = _sfn('lang_str_to_float', dbl, i8p)
        # String methods
        self._fn_str_to_upper  = _sfn('lang_str_to_upper',   i8p, i8p)
        self._fn_str_to_lower  = _sfn('lang_str_to_lower',   i8p, i8p)
        self._fn_str_trim      = _sfn('lang_str_trim',        i8p, i8p)
        self._fn_str_trim_start= _sfn('lang_str_trim_start',  i8p, i8p)
        self._fn_str_trim_end  = _sfn('lang_str_trim_end',    i8p, i8p)
        self._fn_str_contains  = _sfn('lang_str_contains',    i32, i8p, i8p)
        self._fn_str_starts_with=_sfn('lang_str_starts_with', i32, i8p, i8p)
        self._fn_str_ends_with = _sfn('lang_str_ends_with',   i32, i8p, i8p)
        self._fn_str_index_of  = _sfn('lang_str_index_of',    i64, i8p, i8p)
        self._fn_str_slice     = _sfn('lang_str_slice',        i8p, i8p, i64, i64)
        self._fn_str_replace   = _sfn('lang_str_replace',      i8p, i8p, i8p, i8p)
        self._fn_str_repeat    = _sfn('lang_str_repeat',       i8p, i8p, i64)
        # I/O
        self._fn_read_line     = _sfn('lang_read_line',    i8p)
        self._fn_read_file     = _sfn('lang_read_file',    i8p, i8p)
        self._fn_write_file    = _sfn('lang_write_file',   i32, i8p, i8p)
        self._fn_append_file   = _sfn('lang_append_file',  i32, i8p, i8p)
        self._fn_file_exists   = _sfn('lang_file_exists',  i32, i8p)
        self._fn_print_err     = _sfn('lang_print_err',    void, i8p)

    def _declare_builtins(self):
        def _bfn(name, ret, *args):
            if name in self.module.globals:
                return self.module.globals[name]
            f = ir.Function(self.module, ir.FunctionType(ret, list(args)), name=name)
            f.linkage = 'external'; return f
        self._fn_sqrt = _bfn('llvm.sqrt.f64', dbl, dbl)

        # ucontext generator runtime (replaces LLVM coroutine intrinsics)
        def _gfn(name, ret, *args):
            if name in self.module.globals:
                return self.module.globals[name]
            f = ir.Function(self.module, ir.FunctionType(ret, list(args)), name=name)
            f.linkage = 'external'; return f

        self._fn_gen_yield   = _gfn('lang_gen_yield',   i64,  i8p, i64)
        self._fn_gen_resume  = _gfn('lang_gen_resume',  void, i8p, i64)
        self._fn_gen_start   = _gfn('lang_gen_start',   void, i8p, i8p)
        self._fn_gen_cleanup = _gfn('lang_gen_cleanup', void, i8p)

    # ── Type resolution ──────────────────────────────────────────────

    def _llvm_type(self, ty_node) -> tuple[ir.Type, bool]:
        """(llvm_type, is_obj).  Scalars: no ARC.  Everything else: i8*."""
        if ty_node is None:
            return i64, False
        if isinstance(ty_node, TyName):
            tbl = {'int':(i64,False),'float':(dbl,False),
                   'bool':(i1,False),'void':(void,False),
                   'str':(i8p,True),'arr':(i8p,True),'obj':(i8p,True)}
            return tbl.get(ty_node.name, (i8p, True))
        return i8p, True

    # ── Scope helpers ────────────────────────────────────────────────

    def _push_scope(self): self._scopes.append(Scope())
    def _pop_scope(self) -> Scope: return self._scopes.pop()

    def _lookup(self, name: str) -> Optional[VarSlot]:
        for s in reversed(self._scopes):
            v = s.lookup(name)
            if v: return v
        return None

    def _define(self, name: str, slot: VarSlot):
        self._scopes[-1].define(name, slot)

    def _alloca(self, ty: ir.Type, name: str = '') -> ir.Value:
        """Insert alloca in the preamble block, always before its branch."""
        term = self._alloca_blk.terminator
        if term:
            self._alloca_builder.position_before(term)
        return self._alloca_builder.alloca(ty, name=name)

    # ── ARC helpers ──────────────────────────────────────────────────

    def _retain(self, val: ir.Value) -> ir.Value:
        return self._builder.call(self._fn_retain, [val], name='retained')

    def _release(self, val: ir.Value):
        self._builder.call(self._fn_release, [val])

    def _release_scope(self, scope: Scope):
        for slot in scope.all_obj_slots():
            val = self._builder.load(slot.ptr)
            cond = self._builder.icmp_unsigned('!=', val, NULL)
            with self._builder.if_then(cond):
                self._release(val)

    # ── String literals ──────────────────────────────────────────────

    def _str_const(self, s: str) -> ir.Value:
        """Return an i8* pointer to a null-terminated string constant.
        Uses the module-level _str_cache so sub-cogens sharing the same
        module don't emit duplicate string globals."""
        # Use a module-level cache stored as an attribute on the module object
        if not hasattr(self.module, '_sl_str_cache'):
            self.module._sl_str_cache = {}
        cache = self.module._sl_str_cache
        if s not in cache:
            enc   = (s + '\0').encode('utf-8')
            arr_t = ir.ArrayType(i8, len(enc))
            gv    = ir.GlobalVariable(self.module, arr_t,
                                      name=f'_str_{len(cache)}')
            gv.global_constant = True
            gv.linkage         = 'private'
            gv.initializer     = ir.Constant(arr_t, bytearray(enc))
            cache[s] = gv
        return ir.Constant.bitcast(cache[s], i8p)

    # ────────────────────────────────────────────────────────────────
    # Top-level
    # ────────────────────────────────────────────────────────────────

    def visit_Program(self, node: Program):
        self._push_scope()
        for stmt in node.stmts:
            self.visit(stmt)
        self._pop_scope()

    # ────────────────────────────────────────────────────────────────
    # Class definition
    # ────────────────────────────────────────────────────────────────

    def visit_ClassDef(self, node: ClassDef):
        """
        Emit everything for a class:
          1. Build struct type and vtable type
          2. Forward-declare dtor, visitor, constructor, all methods
          3. Emit the vtable global (references the forward-declared functions)
          4. Fill in all function bodies
        """
        name = node.name

        # ── 1. Resolve field types ───────────────────────────────────
        field_names  = [p.name for p in node.fields]
        field_lltys  = []
        field_is_obj = {}
        field_llty_map = {}
        for p in node.fields:
            ty, is_obj = self._llvm_type(p.type_)
            field_lltys.append(ty)
            field_is_obj[p.name]  = is_obj
            field_llty_map[p.name] = ty

        struct_ty = ir.LiteralStructType(HEADER_FIELDS + field_lltys)

        # ── 2. Collect method signatures ─────────────────────────────
        method_defs = [s for s in node.body.stmts if isinstance(s, FnDef)]
        # :init is excluded from vtable — it's a constructor hook, not user-callable
        n_methods   = sum(1 for m in method_defs if m.name != 'init')

        # vtable: [i32 type_id, i32 method_count, i8* dtor, i8* visitor, i8*× n_methods]
        vtable_ty = ir.LiteralStructType([i32, i32] + [i8p] * (2 + n_methods))

        # stable name-based type_id
        type_id = int(hashlib.md5(name.encode()).hexdigest()[:8], 16) & 0xFFFFFFFF

        # ── 3. Build ClassInfo skeleton ──────────────────────────────
        # We need a placeholder vtable_gv to build ClassInfo before bodies
        vtable_gv = ir.GlobalVariable(self.module, vtable_ty,
                                      name=f'{name}_vtable')
        vtable_gv.linkage = 'private'

        ci = ClassInfo(
            name      = name,
            struct_ty = struct_ty,
            vtable_ty = vtable_ty,
            vtable_gv = vtable_gv,
            type_id   = type_id,
        )
        for i, p in enumerate(node.fields):
            ci.field_names.append(p.name)
            ci.field_index[p.name]  = i
            ci.field_type[p.name]   = field_llty_map[p.name]
            ci.field_is_obj[p.name] = field_is_obj[p.name]

        self._classes[name] = ci

        # ── 4. Forward-declare dtor / visitor / constructor ──────────
        dtor_ty = ir.FunctionType(void, [i8p])
        ci.dtor = ir.Function(self.module, dtor_ty, name=f'{name}_dtor')
        ci.dtor.linkage = 'private'

        # visitor: void(i8* self, i8* cb_raw, i8* ctx)
        vis_ty = ir.FunctionType(void, [i8p, i8p, i8p])
        ci.visitor = ir.Function(self.module, vis_ty, name=f'{name}_visit')
        ci.visitor.linkage = 'private'

        # constructor: i8*(field_ty...)
        ctor_param_tys = [field_llty_map[p.name] for p in node.fields]
        ctor_ty = ir.FunctionType(i8p, ctor_param_tys)
        ci.constructor = ir.Function(self.module, ctor_ty,
                                     name=f'{name}_init')
        ci.constructor.linkage = 'external'

        # ── 5. Forward-declare methods ───────────────────────────────
        # :init:@ is a special post-allocation hook — declared and emitted
        # like a normal method but excluded from the vtable slot list so it
        # isn't exposed as a user-callable dispatch target.
        slot_idx = 0
        for mdef in method_defs:
            fn_name, fn_ty = self._method_signature(ci, mdef)
            mfn = ir.Function(self.module, fn_ty, name=fn_name)
            mfn.linkage = 'external'
            ci.method_fnty[mdef.name] = fn_ty
            ci.method_fn[mdef.name]   = mfn
            if mdef.name != 'init':   # exclude :init from vtable
                ci.method_names.append(mdef.name)
                ci.method_slot[mdef.name] = slot_idx
                slot_idx += 1

        # ── 6. Emit vtable constant ──────────────────────────────────
        def _as_i8p(fn): return ir.Constant.bitcast(fn, i8p)

        method_ptrs = [_as_i8p(ci.method_fn[m]) for m in ci.method_names]
        vtable_gv.initializer = ir.Constant(vtable_ty, [
            ir.Constant(i32, type_id),
            ir.Constant(i32, n_methods),
            _as_i8p(ci.dtor),
            _as_i8p(ci.visitor),
            *method_ptrs,
        ])
        vtable_gv.global_constant = True

        # ── 7. Emit function bodies ──────────────────────────────────
        self._emit_class_dtor(ci)
        self._emit_class_visitor(ci)
        self._emit_class_ctor(ci, node.fields)

        prev_class = self._current_class
        self._current_class = ci
        for mdef in method_defs:
            self._emit_method(ci, mdef)
        self._current_class = prev_class

    # ── Method signature helper ──────────────────────────────────────

    def _method_signature(self, ci: ClassInfo,
                          mdef: FnDef) -> tuple[str, ir.FunctionType]:
        """Return (llvm_fn_name, FunctionType) for a method."""
        # :init maps to ClassName__init__ to avoid clash with ClassName_init (the constructor)
        mname = '__init__' if mdef.name == 'init' else mdef.name
        fn_name = f'{ci.name}_{mname}'

        param_tys: list[ir.Type] = []
        # Receiver
        if mdef.receiver in ('instance', 'ext'):
            param_tys.append(i8p)   # self = i8*
        elif mdef.receiver == 'class':
            param_tys.append(i8p)   # cls = vtable ptr

        # Other params (skip the first Param if it's 'self'/'cls')
        start = 1 if (mdef.receiver in ('instance','ext','class')
                      and mdef.params) else 0
        for p in mdef.params[start:]:
            ty, _ = self._llvm_type(p.type_)
            param_tys.append(ty)

        ret_ty, _ = self._llvm_type(mdef.return_type) \
            if mdef.return_type else (i64, False)
        if isinstance(mdef.return_type, TyName) and \
                mdef.return_type.name == 'void':
            ret_ty = void

        return fn_name, ir.FunctionType(ret_ty, param_tys)

    # ── Dtor body ────────────────────────────────────────────────────

    def _emit_class_dtor(self, ci: ClassInfo):
        """
        void ClassName_dtor(i8* raw_self)
          Cast to struct, release every object-typed field.
        """
        fn  = ci.dtor
        blk = fn.append_basic_block('entry')
        bld = ir.IRBuilder(blk)
        raw = fn.args[0]
        raw.name = 'self'

        typed = bld.bitcast(raw, ci.struct_ty.as_pointer(), name='typed')

        for fname in ci.field_names:
            if not ci.field_is_obj[fname]:
                continue
            idx  = ci.struct_field_index(fname)
            slot = bld.gep(typed,
                           [ir.Constant(i32, 0), ir.Constant(i32, idx)],
                           inbounds=True)
            val  = bld.load(slot)
            cond = bld.icmp_unsigned('!=', val, NULL)
            with bld.if_then(cond):
                bld.call(self._fn_release, [val])

        bld.ret_void()

    # ── Visitor body ─────────────────────────────────────────────────

    def _emit_class_visitor(self, ci: ClassInfo):
        """
        void ClassName_visit(i8* raw_self, i8* cb_raw, i8* ctx)
          For each object-typed field, call cb(field_val, ctx).
        """
        fn        = ci.visitor
        blk       = fn.append_basic_block('entry')
        bld       = ir.IRBuilder(blk)
        raw, cb_raw, ctx = fn.args
        raw.name = 'self'; cb_raw.name = 'cb'; ctx.name = 'ctx'

        # Cast the callback to its true type: void(i8*, i8*)*
        cb_fty   = ir.FunctionType(void, [i8p, i8p])
        cb_typed = bld.bitcast(cb_raw, cb_fty.as_pointer(), name='cb_typed')

        typed = bld.bitcast(raw, ci.struct_ty.as_pointer(), name='typed')

        for fname in ci.field_names:
            if not ci.field_is_obj[fname]:
                continue
            idx  = ci.struct_field_index(fname)
            slot = bld.gep(typed,
                           [ir.Constant(i32, 0), ir.Constant(i32, idx)],
                           inbounds=True)
            val  = bld.load(slot)
            cond = bld.icmp_unsigned('!=', val, NULL)
            with bld.if_then(cond):
                bld.call(cb_typed, [val, ctx])

        bld.ret_void()

    # ── Constructor body ──────────────────────────────────────────────

    def _emit_class_ctor(self, ci: ClassInfo, fields: list[Param]):
        """
        i8* ClassName_init(field0, field1, ...)
          Allocate via lang_alloc, store vtable ptr, store each field.
          Returns i8* (the ARC object pointer; refcount = 1).
        """
        fn  = ci.constructor
        blk = fn.append_basic_block('entry')
        bld = ir.IRBuilder(blk)

        # sizeof(struct) in bytes
        size_val = ir.Constant(i64,
                   ci.struct_ty.get_abi_size(binding.create_target_data(
                       self.module.data_layout or '')) )

        # Cast vtable global to i8*
        vtable_raw = ir.Constant.bitcast(ci.vtable_gv, i8p)

        raw   = bld.call(self._fn_alloc, [size_val, vtable_raw], name='raw')
        typed = bld.bitcast(raw, ci.struct_ty.as_pointer(), name='typed')

        # Store each user field
        for llvm_arg, p in zip(fn.args, fields):
            llvm_arg.name = p.name
            fi   = ci.struct_field_index(p.name)
            slot = bld.gep(typed,
                           [ir.Constant(i32, 0), ir.Constant(i32, fi)],
                           inbounds=True)
            bld.store(llvm_arg, slot)
            # If the field is an object, retain it (the caller still owns
            # the original reference; we're taking a new one)
            if ci.field_is_obj[p.name]:
                cond = bld.icmp_unsigned('!=', llvm_arg, NULL)
                with bld.if_then(cond):
                    bld.call(self._fn_retain, [llvm_arg])

        # ── Call :init:@ if defined ───────────────────────────────────
        # If the class defines an  :init:@[void]  method, call it now
        # so the object can run post-allocation logic (defaults, validation,
        # derived fields) before the caller receives it.
        if 'init' in ci.method_fn:
            init_fn = ci.method_fn['init']
            # init must be void and take only self (i8*)
            if (isinstance(init_fn.type.pointee.return_type, ir.VoidType)
                    and len(init_fn.type.pointee.args) == 1):
                bld.call(init_fn, [raw])

        bld.ret(raw)

    # ── Method body ───────────────────────────────────────────────────

    def _emit_method(self, ci: ClassInfo, mdef: FnDef):
        """Emit a method body inside the class context."""
        fn_name, _ = self._method_signature(ci, mdef)
        fn          = ci.method_fn[mdef.name]

        if mdef.is_gen or mdef.is_async:
            blk = fn.append_basic_block('entry')
            ir.IRBuilder(blk).ret(NULL)
            return

        # Preamble block for allocas
        alloca_blk = fn.append_basic_block('alloca_preamble')
        entry_blk  = fn.append_basic_block('entry')

        saved = (self._fn, self._fn_node, self._builder,
                 self._alloca_builder, self._alloca_blk)

        self._fn           = fn
        self._fn_node      = mdef
        self._alloca_blk   = alloca_blk
        self._alloca_builder = ir.IRBuilder(alloca_blk)
        self._builder      = ir.IRBuilder(entry_blk)

        # Determine which params to bind and their names
        param_names = []
        if mdef.receiver in ('instance', 'ext', 'class'):
            param_names.append('self')
        start = 1 if (mdef.receiver in ('instance','ext','class')
                      and mdef.params) else 0
        for p in mdef.params[start:]:
            param_names.append(p.name)

        # Alloca preamble, then store args in entry
        preamble_ptrs: list[ir.Value] = []
        preamble_tys:  list[ir.Type]  = []
        for llvm_arg, pname in zip(fn.args, param_names):
            llvm_arg.name = pname
            ptr = self._alloca_builder.alloca(llvm_arg.type, name=pname)
            preamble_ptrs.append(ptr)
            preamble_tys.append(llvm_arg.type)

        self._alloca_builder.branch(entry_blk)

        self._push_scope()
        for llvm_arg, ptr, pname, pty in zip(
                fn.args, preamble_ptrs, param_names, preamble_tys):
            self._builder.store(llvm_arg, ptr)
            # Params are borrowed — caller owns, never release at scope exit
            self._define(pname, VarSlot(ptr=ptr, ty=pty, is_obj=False))

        self._emit_block(mdef.body)

        ret_ty = fn.type.pointee.return_type
        if not self._builder.block.is_terminated:
            self._release_scope(self._scopes[-1])
            if isinstance(ret_ty, ir.VoidType):
                self._builder.ret_void()
            else:
                self._builder.ret(ir.Constant(ret_ty, 0))

        self._pop_scope()

        (self._fn, self._fn_node, self._builder,
         self._alloca_builder, self._alloca_blk) = saved

    # ────────────────────────────────────────────────────────────────
    # Function definition (standalone, not inside a class)
    # ────────────────────────────────────────────────────────────────

    def visit_FnDef(self, node: FnDef):
        # When inside a ~[ns] block, prefix names so  math::pi  works
        ns_prefix = f'{self._current_ns}__' if self._current_ns else ''
        if self._current_ns and node.name != 'main':
            # Register in namespace table (original name → prefixed IR name)
            self._namespaces[self._current_ns][node.name] = f'{ns_prefix}{node.name}'
        param_tys:    list[ir.Type] = []
        is_obj_params:list[bool]    = []

        for p in node.params:
            if p.name == 'self':
                param_tys.append(i8p); is_obj_params.append(True); continue
            ty, is_obj = self._llvm_type(p.type_)
            param_tys.append(ty); is_obj_params.append(is_obj)

        if node.return_type is None:
            ret_ty = i64
        else:
            ret_ty, _ = self._llvm_type(node.return_type)
        if isinstance(node.return_type, TyName) and \
                node.return_type.name == 'void':
            ret_ty = void

        # Generator and async: delegate to dedicated emitter before creating
        # any regular ir.Function objects (gen emitter makes its own).
        if node.is_gen or node.is_async:
            self._emit_gen_fn(node, param_tys=param_tys)
            return

        ir_name = f'{ns_prefix}{node.name}' if ns_prefix else node.name
        fn_ty = ir.FunctionType(ret_ty, param_tys)
        fn    = ir.Function(self.module, fn_ty, name=ir_name)
        fn.linkage = 'external'

        # Register under BOTH the local name (inside the ns block) and the
        # qualified name (ns::fn) so both  pi  and  math::pi  resolve.
        gv = ir.GlobalVariable(self.module, fn_ty.as_pointer(),
                               name=f'_fnptr_{ir_name}')
        gv.global_constant = True; gv.linkage = 'private'
        gv.initializer = fn
        slot = VarSlot(ptr=gv, ty=fn_ty.as_pointer(), is_obj=False)
        self._define(node.name, slot)  # local name (inside ns block)
        if self._current_ns:
            # Also register the qualified name globally for cross-file access
            qualified = f'{self._current_ns}::{node.name}'
            if len(self._scopes) > 0:
                self._scopes[0].define(qualified, slot)  # global scope

        alloca_blk = fn.append_basic_block('alloca_preamble')
        entry_blk  = fn.append_basic_block('entry')

        self._fn           = fn
        self._fn_node      = node
        self._alloca_blk   = alloca_blk
        self._alloca_builder = ir.IRBuilder(alloca_blk)
        self._builder      = ir.IRBuilder(entry_blk)

        # Alloca all params in preamble
        preamble_ptrs = []
        for p, pty in zip(node.params, param_tys):
            ptr = self._alloca_builder.alloca(pty, name=p.name)
            preamble_ptrs.append(ptr)
        self._alloca_builder.branch(entry_blk)

        # Store args — params are BORROWED (+0), never released at scope exit
        self._push_scope()
        for llvm_arg, p, ptr, pty in zip(
                fn.args, node.params, preamble_ptrs, param_tys):
            llvm_arg.name = p.name
            self._builder.store(llvm_arg, ptr)
            # is_obj=False: params are borrowed refs owned by the caller
            self._define(p.name, VarSlot(ptr=ptr, ty=pty, is_obj=False))

        self._emit_block(node.body)

        if not self._builder.block.is_terminated:
            self._release_scope(self._scopes[-1])
            if isinstance(ret_ty, ir.VoidType):
                self._builder.ret_void()
            else:
                self._builder.ret(ir.Constant(ret_ty, 0))

        self._pop_scope()
        self._fn = self._fn_node = self._builder = None
        self._alloca_builder = self._alloca_blk = None

    def visit_MixinDef(self, node: MixinDef):
        for stmt in node.body.stmts:
            if isinstance(stmt, FnDef):
                self.visit_FnDef(stmt)

    # ────────────────────────────────────────────────────────────────
    # ── Generator emission  (ucontext / swapcontext based) ─────────────
    #
    # For  |:counter:n_int[|int]  the codegen produces:
    #
    #   counter_init(n: i64) -> i8*
    #     Allocates the 64-byte GenWrapper (ARC object), stores n in the
    #     inline params area (offset 64), calls lang_gen_start(wrapper, body_fn)
    #     which runs body_fn until its first lang_gen_yield, then returns wrapper.
    #
    #   counter_body(gen_raw: i8*) -> void
    #     Reads params from gen_raw+64, runs the user body.
    #     Each  ->| val  compiles to  call lang_gen_yield(gen_raw, val)  which
    #     suspends and returns the value sent in by the caller.
    #
    #   counter_gen_dtor(self: i8*) -> void
    #     Called by ARC when wrapper's refcount reaches 0.
    #     Calls lang_gen_cleanup() to free the stack and ucontext pair.
    #
    # GenWrapper layout (32-byte LangObj hdr + 32-byte gen header = 64 bytes):
    #   [5]  i8*  ctx        → LangGenCtx* (opaque, allocated by lang_gen_start)
    #   [6]  i64  yield_val  (written by lang_gen_yield)
    #   [7]  i64  send_val   (written by lang_gen_resume)
    #   [8]  i32  done       (set to 1 by trampoline when body returns)
    #   [9]  i32  _pad
    #   [64+]    user params (inline, 8 bytes per param)

    def _emit_gen_fn(self, node: FnDef, param_tys: list) -> None:
        name = node.name

        yield_ty, _ = (self._llvm_type(node.return_type.yield_t)
                       if isinstance(node.return_type, TyGen) else (i64, False))
        send_ty = yield_ty

        # Fixed 64-byte wrapper (no user fields — params stored inline after it)
        wrapper_ty = ir.LiteralStructType(
            HEADER_FIELDS + [i8p, yield_ty, send_ty, i32, i32])
        vtable_ty  = ir.LiteralStructType([i32, i32, i8p, i8p])

        import hashlib as _hl
        type_id = int(_hl.md5(name.encode()).hexdigest()[:8], 16) & 0xFFFFFFFF

        vtable_gv = ir.GlobalVariable(self.module, vtable_ty,
                                       name=f'{name}_gen_vtable')
        vtable_gv.linkage = 'private'

        gi = GenInfo(name=name, yield_ty=yield_ty, send_ty=send_ty,
                     wrapper_ty=wrapper_ty, vtable_ty=vtable_ty,
                     vtable_gv=vtable_gv, param_tys=list(param_tys))
        self._gens[name] = gi

        # Forward-declare dtor
        gi.dtor_fn = ir.Function(self.module,
                                  ir.FunctionType(void, [i8p]),
                                  name=f'{name}_gen_dtor')
        gi.dtor_fn.linkage = 'private'

        # Body function: void name_body(i8* gen_raw)
        gi.body_fn = ir.Function(self.module,
                                  ir.FunctionType(void, [i8p]),
                                  name=f'{name}_body')
        gi.body_fn.linkage = 'private'

        # Init function: i8* name_init(params...)
        gi.init_fn = ir.Function(self.module,
                                  ir.FunctionType(i8p, list(param_tys)),
                                  name=f'{name}_init')
        gi.init_fn.linkage = 'external'

        # Vtable
        def _p(f): return ir.Constant.bitcast(f, i8p)
        vtable_gv.initializer = ir.Constant(vtable_ty, [
            ir.Constant(i32, type_id),
            ir.Constant(i32, 0),
            _p(gi.dtor_fn),
            NULL,
        ])
        vtable_gv.global_constant = True

        # Expose init under the generator name
        gv = ir.GlobalVariable(self.module, gi.init_fn.type,
                                name=f'_fnptr_{name}')
        gv.global_constant = True; gv.linkage = 'private'
        gv.initializer = gi.init_fn
        self._define(name, VarSlot(ptr=gv, ty=gi.init_fn.type, is_obj=False))

        self._emit_gen_dtor(gi)
        self._emit_gen_init(gi, node, param_tys)
        self._emit_gen_body(gi, node, param_tys)

    # ── Dtor ──────────────────────────────────────────────────────────

    def _emit_gen_dtor(self, gi: GenInfo):
        fn  = gi.dtor_fn
        blk = fn.append_basic_block('entry')
        bld = ir.IRBuilder(blk)
        bld.call(self._fn_gen_cleanup, [fn.args[0]])
        bld.ret_void()

    # ── Init ──────────────────────────────────────────────────────────

    def _emit_gen_init(self, gi: GenInfo, node: FnDef, param_tys: list):
        """
        i8* name_init(params...)
          Allocates the GenWrapper + inline params, stores params,
          then calls lang_gen_start to run the body until its first yield.
        """
        fn  = gi.init_fn
        blk = fn.append_basic_block('entry')
        bld = ir.IRBuilder(blk)

        # Total size: 64-byte header + 8 bytes per param (all padded to i64)
        total = GEN_PARAMS_OFFSET + 8 * len(param_tys)
        vtable_raw = ir.Constant.bitcast(gi.vtable_gv, i8p)
        gen_raw = bld.call(self._fn_alloc,
                            [ir.Constant(i64, total), vtable_raw],
                            name='gen')

        # Zero ctx field (lang_alloc zeroes, but be explicit)
        typed = bld.bitcast(gen_raw, gi.wrapper_ty.as_pointer())
        ctx_ptr = bld.gep(typed, [ir.Constant(i32,0), ir.Constant(i32, GEN_CTX_IDX)],
                           inbounds=True)
        bld.store(NULL, ctx_ptr)

        # Store user params at GEN_PARAMS_OFFSET + 8*i
        off = GEN_PARAMS_OFFSET
        for llvm_arg, pty in zip(fn.args, param_tys):
            p_raw = bld.gep(gen_raw, [ir.Constant(i64, off)], inbounds=True)
            p_ptr = bld.bitcast(p_raw, pty.as_pointer())
            bld.store(llvm_arg, p_ptr)
            off += 8  # 8-byte stride for all param types

        # lang_gen_start: run body until first yield / completion
        body_ptr = ir.Constant.bitcast(gi.body_fn, i8p)
        bld.call(self._fn_gen_start, [gen_raw, body_ptr])

        bld.ret(gen_raw)

    # ── Body (runs on its own ucontext stack) ─────────────────────────

    def _emit_gen_body(self, gi: GenInfo, node: FnDef, param_tys: list):
        """
        void name_body(i8* gen_raw)
          Reads user params from gen_raw+GEN_PARAMS_OFFSET, runs the body.
          Each ->| val compiles to call lang_gen_yield(gen_raw, val).
        """
        fn = gi.body_fn

        alloca_blk = fn.append_basic_block('alloca_preamble')
        entry_blk  = fn.append_basic_block('entry')
        body_blk   = fn.append_basic_block('body_start')

        bld_pre = ir.IRBuilder(alloca_blk)
        gen_arg = fn.args[0]; gen_arg.name = 'gen_raw'

        # Alloca for gen_raw (so body can reload it after any call)
        gen_slot = bld_pre.alloca(i8p, name='_gen_raw')
        bld_pre.store(gen_arg, gen_slot)

        # Load + alloca each user param from the inline params area
        param_slots = []
        off = GEN_PARAMS_OFFSET
        for p, pty in zip(node.params, param_tys):
            p_raw = bld_pre.gep(gen_arg, [ir.Constant(i64, off)], inbounds=True)
            p_ptr = bld_pre.bitcast(p_raw, pty.as_pointer())
            val   = bld_pre.load(p_ptr, name=p.name)
            slot  = bld_pre.alloca(pty, name=p.name)
            bld_pre.store(val, slot)
            param_slots.append((p.name, slot, pty))
            off += 8

        bld_pre.branch(entry_blk)
        ir.IRBuilder(entry_blk).branch(body_blk)

        # Save and restore per-function state (body is a nested function)
        saved = (self._fn, self._fn_node, self._builder,
                 self._alloca_builder, self._alloca_blk,
                 self._current_gen, self._gen_wrapper_slot)

        self._fn              = fn
        self._fn_node         = node
        self._alloca_blk      = alloca_blk
        self._alloca_builder  = bld_pre
        self._builder         = ir.IRBuilder(body_blk)
        self._current_gen     = gi
        self._gen_wrapper_slot = gen_slot      # body loads gen_raw from here

        self._push_scope()
        for pname, slot, pty in param_slots:
            self._define(pname, VarSlot(ptr=slot, ty=pty, is_obj=False))

        self._emit_block(node.body)

        if not self._builder.block.is_terminated:
            self._pop_scope()
            self._builder.ret_void()
        else:
            self._pop_scope()

        (self._fn, self._fn_node, self._builder,
         self._alloca_builder, self._alloca_blk,
         self._current_gen, self._gen_wrapper_slot) = saved

    def visit_IfaceDef(self, node: IfaceDef):
        pass   # no bodies to emit

    def visit_ExternC(self, node: ExternC):
        """
        ~C :lang_abs:n_int[int]

        Emits  declare external i64 @lang_abs(i64)  in the LLVM module
        and registers  lang_abs  in the current scope so SL code can call it.
        If inside a ~[ns] block, also registers  ns::lang_abs.
        """
        param_tys = []
        for p in node.params:
            if p.name == 'self':
                param_tys.append(i8p); continue
            ty, _ = self._llvm_type(p.type_) if p.type_ else (i64, False)
            param_tys.append(ty)

        ret_ty, _ = self._llvm_type(node.return_type) if node.return_type else (void, False)
        fn_ty = ir.FunctionType(ret_ty, param_tys)

        # Reuse existing declaration if already in module
        if node.c_name in self.module.globals:
            fn     = self.module.globals[node.c_name]
            # Use the ACTUAL type of the existing function, not the declared one.
            # This avoids mismatches when the same C function is declared twice
            # (once in _declare_runtime with the correct i32 type, and again in
            # a stdlib ~C block with [int] → i64).
            fn_ty  = fn.type.pointee
        else:
            fn = ir.Function(self.module, fn_ty, name=node.c_name)
            fn.linkage = 'external'

        # Register under the C name in current scope
        gv_name = f'_fnptr_C_{node.c_name}'
        if gv_name in self.module.globals:
            gv = self.module.globals[gv_name]
        else:
            gv = ir.GlobalVariable(self.module, fn_ty.as_pointer(), name=gv_name)
            gv.global_constant = True
            gv.linkage = 'private'
            gv.initializer = fn

        slot = VarSlot(ptr=gv, ty=fn_ty.as_pointer(), is_obj=False)
        self._define(node.c_name, slot)

        # Also register in namespace if inside ~[ns]
        if self._current_ns:
            qualified = f'{self._current_ns}::{node.c_name}'
            self._scopes[0].define(qualified, slot)
            self._namespaces[self._current_ns][node.c_name] = node.c_name

    def visit_ImportStmt(self, node: ImportStmt):
        """
        ~> geometry              loads geometry.sl (or geometry.slb) and
                                 makes all its ~[ns] symbols available as
                                 geometry::Symbol in this module.

        ~> geometry:Vec2,dot     selective import — puts Vec2 and dot
                                 directly into local scope without prefix.

        ~> "path/to/file"        explicit path (str literal in parser gives
                                 module name = basename without extension).

        Resolution order:
          1. Same directory as the current source file
          2. ~/.sl/packages/  (installed bundles)
          3. <compiler_dir>/stdlib/  (built-in stdlib)
        """
        import os, zipfile
        from lexer import lex
        from parser import Parser as _Parser

        module_name = node.module
        source_path = None

        # Search order
        search_dirs = []
        if self._source_file:
            search_dirs.append(os.path.dirname(os.path.abspath(self._source_file)))
        search_dirs.append(os.path.expanduser('~/.sl/packages'))
        compiler_dir = os.path.dirname(os.path.abspath(__file__))
        search_dirs.append(os.path.join(compiler_dir, 'stdlib'))

        for d in search_dirs:
            for ext in ('.sl', '.slb'):
                candidate = os.path.join(d, module_name + ext)
                if os.path.exists(candidate):
                    source_path = candidate
                    break
            if source_path:
                break

        if source_path is None:
            raise CodegenError(
                f"module '{module_name}' not found (searched: {', '.join(search_dirs)})", node)

        # Load source — .slb is a ZIP, .sl is plain text
        if source_path.endswith('.slb'):
            with zipfile.ZipFile(source_path) as zf:
                # Find the main .sl file in src/
                sl_files = [n for n in zf.namelist()
                            if n.startswith('src/') and n.endswith('.sl')]
                src_text = '\n'.join(zf.read(f).decode() for f in sl_files)
        else:
            with open(source_path, encoding='utf-8') as f:
                src_text = f.read()

        # Compile into a sub-Codegen that shares our LLVM module
        sub = Codegen(source_path, module=self.module)
        tree = _Parser(lex(src_text, source_path), source_path).parse()
        sub.visit(tree)

        # Merge classes from sub-codegen into this one
        for cls_name, ci in sub._classes.items():
            self._classes[cls_name] = ci

        # Merge namespaces into this codegen
        for ns_name, symbols in sub._namespaces.items():
            if ns_name not in self._namespaces:
                self._namespaces[ns_name] = {}
            self._namespaces[ns_name].update(symbols)
            # Register qualified names in our global scope
            for sym_name, ir_name in symbols.items():
                qualified = f'{ns_name}::{sym_name}'
                if ir_name in self.module.globals:
                    gv_name = f'_fnptr_{ir_name}'
                    if gv_name in self.module.globals:
                        gv = self.module.globals[gv_name]
                        slot = VarSlot(ptr=gv, ty=gv.type.pointee, is_obj=False)
                        self._scopes[0].define(qualified, slot)

        # Selective import:  ~> geometry:Vec2,dot
        if node.names:
            for sym in node.names:
                # Look for  sym  in any namespace from the imported module
                for ns_name, symbols in sub._namespaces.items():
                    if sym in symbols:
                        ir_name = symbols[sym]
                        gv_name = f'_fnptr_{ir_name}'
                        if gv_name in self.module.globals:
                            gv = self.module.globals[gv_name]
                            slot = VarSlot(ptr=gv, ty=gv.type.pointee, is_obj=False)
                            self._scopes[0].define(sym, slot)  # unqualified in local scope
                            break
        elif node.alias:
            # ~> geometry as geo  → geo::Symbol works
            for sym_name, ir_name in sub._namespaces.get(module_name, {}).items():
                qualified = f'{node.alias}::{sym_name}'
                gv_name = f'_fnptr_{ir_name}'
                if gv_name in self.module.globals:
                    gv = self.module.globals[gv_name]
                    self._scopes[0].define(qualified, VarSlot(
                        ptr=gv, ty=gv.type.pointee, is_obj=False))
    def visit_Namespace(self, node: Namespace):
        """
        ~[math]
            :pi[float] -> 3.14159
            .Vec2:x_int,y_int
                ...

        All symbols defined inside are registered under  math::name  in
        _namespaces so that  math::pi  and  math::Vec2  resolve correctly.
        The symbols are also emitted into the LLVM module with the prefix
        applied to their IR name.
        """
        prev = self._current_ns
        self._current_ns = node.name
        if node.name not in self._namespaces:
            self._namespaces[node.name] = {}
        self._push_scope()
        self._emit_block(node.body)
        self._pop_scope()
        self._current_ns = prev
    def visit_MixinAttach(self, node: MixinAttach): pass

    # ────────────────────────────────────────────────────────────────
    # Block emission
    # ────────────────────────────────────────────────────────────────

    def _emit_block(self, block: Block):
        self._push_scope()
        for stmt in block.stmts:
            if self._builder and self._builder.block.is_terminated:
                break
            self.visit(stmt)
        scope = self._pop_scope()
        if self._builder and not self._builder.block.is_terminated:
            self._release_scope(scope)

    # ────────────────────────────────────────────────────────────────
    # Statements
    # ────────────────────────────────────────────────────────────────

    def visit_ExprStmt(self, node: ExprStmt): self.visit(node.expr)
    def visit_YieldStmt(self, node: YieldStmt):
        """Statement-level yield: ->| val  compiles the same as expression yield."""
        if self._current_gen is not None:
            from parser import YieldExpr
            self.visit_YieldExpr(YieldExpr(node.value, node.line, node.col))
    def visit_YieldFromStmt(self, node: YieldFromStmt): pass

    def visit_ReturnStmt(self, node: ReturnStmt):
        for s in reversed(self._scopes):
            self._release_scope(s)
        if node.value is None:
            self._builder.ret_void(); return
        val = self.visit(node.value)
        if val is None:
            self._builder.ret_void(); return
        # Coerce return value to match the declared function return type
        ret_ty = self._fn.type.pointee.return_type
        if isinstance(ret_ty, ir.VoidType):
            self._builder.ret_void(); return
        if isinstance(ret_ty, ir.IntType) and isinstance(val.type, ir.IntType):
            if val.type.width < ret_ty.width:
                val = self._builder.sext(val, ret_ty)
            elif val.type.width > ret_ty.width:
                val = self._builder.trunc(val, ret_ty)
        elif isinstance(ret_ty, ir.DoubleType) and isinstance(val.type, ir.IntType):
            val = self._builder.sitofp(val, ret_ty)
        elif isinstance(ret_ty, ir.IntType) and isinstance(val.type, ir.DoubleType):
            val = self._builder.fptosi(val, ret_ty)
        self._builder.ret(val)

    # ── If ───────────────────────────────────────────────────────────

    def visit_IfStmt(self, node: IfStmt):
        fn   = self._fn
        cond = self._coerce_bool(self.visit(node.cond))

        if not node.elifs and node.else_ is None:
            with self._builder.if_then(cond):
                self._emit_block(node.then)
            return

        merge_blk = fn.append_basic_block('if_merge')
        else_start = merge_blk

        # Build elif chain back-to-front
        elif_chain = []
        for ec, eb in reversed(node.elifs):
            tb = fn.append_basic_block('elif_test')
            bb = fn.append_basic_block('elif_body')
            elif_chain.insert(0, (ec, eb, tb, bb))
            else_start = tb

        else_blk = None
        if node.else_ is not None:
            else_blk   = fn.append_basic_block('else')
            else_start = else_blk

        then_blk = fn.append_basic_block('then')
        self._builder.cbranch(cond, then_blk, else_start)

        self._builder.position_at_end(then_blk)
        self._emit_block(node.then)
        if not self._builder.block.is_terminated:
            self._builder.branch(merge_blk)

        for i, (ec, eb, tb, bb) in enumerate(elif_chain):
            self._builder.position_at_end(tb)
            nxt = elif_chain[i+1][2] if i+1 < len(elif_chain) else (else_blk or merge_blk)
            c2  = self._coerce_bool(self.visit(ec))
            self._builder.cbranch(c2, bb, nxt)
            self._builder.position_at_end(bb)
            self._emit_block(eb)
            if not self._builder.block.is_terminated:
                self._builder.branch(merge_blk)

        if else_blk:
            self._builder.position_at_end(else_blk)
            self._emit_block(node.else_)
            if not self._builder.block.is_terminated:
                self._builder.branch(merge_blk)

        self._builder.position_at_end(merge_blk)

    # ── While ────────────────────────────────────────────────────────

    def visit_WhileStmt(self, node: WhileStmt):
        fn = self._fn
        cond_b  = fn.append_basic_block('while_cond')
        body_b  = fn.append_basic_block('while_body')
        after_b = fn.append_basic_block('while_after')
        self._builder.branch(cond_b)
        self._builder.position_at_end(cond_b)
        cond = self._coerce_bool(self.visit(node.cond))
        self._builder.cbranch(cond, body_b, after_b)
        self._loop_stack.append((cond_b, after_b))
        self._builder.position_at_end(body_b)
        self._emit_block(node.body)
        if not self._builder.block.is_terminated:
            self._builder.branch(cond_b)
        self._loop_stack.pop()
        self._builder.position_at_end(after_b)

    # ── For ──────────────────────────────────────────────────────────

    def visit_ForStmt(self, node: ForStmt):
        if isinstance(node.iterable, RangeExpr):
            self._emit_range_loop(node, node.iterable)
        else:
            self._emit_block(node.body)   # stub

    def _emit_range_loop(self, node: ForStmt, r: RangeExpr):
        fn        = self._fn
        start_val = self.visit(r.start)
        end_val   = self.visit(r.end)
        step_val  = self.visit(r.step) if r.step else ir.Constant(i64, 1)

        ptr = self._alloca(i64, name=node.var)
        self._builder.store(start_val, ptr)
        self._push_scope()
        self._define(node.var, VarSlot(ptr=ptr, ty=i64, is_obj=False))

        cond_b  = fn.append_basic_block('for_cond')
        body_b  = fn.append_basic_block('for_body')
        incr_b  = fn.append_basic_block('for_incr')
        after_b = fn.append_basic_block('for_after')

        self._builder.branch(cond_b)
        self._builder.position_at_end(cond_b)
        cur = self._builder.load(ptr, name=node.var)
        op  = '<=' if r.inclusive else '<'
        self._builder.cbranch(self._builder.icmp_signed(op, cur, end_val),
                               body_b, after_b)

        self._loop_stack.append((incr_b, after_b))
        self._builder.position_at_end(body_b)
        self._emit_block(node.body)
        if not self._builder.block.is_terminated:
            self._builder.branch(incr_b)
        self._loop_stack.pop()

        self._builder.position_at_end(incr_b)
        self._builder.store(
            self._builder.add(self._builder.load(ptr), step_val), ptr)
        self._builder.branch(cond_b)

        self._pop_scope()
        self._builder.position_at_end(after_b)

    def visit_BreakStmt(self, node):
        _, brk = self._loop_stack[-1]; self._builder.branch(brk)
    def visit_ContinueStmt(self, node):
        cnt, _ = self._loop_stack[-1]; self._builder.branch(cnt)

    def visit_TryCatch(self, node: TryCatch):
        self._emit_block(node.body)
        if node.finally_: self._emit_block(node.finally_)

    def visit_AssertStmt(self, node: AssertStmt):
        fn = self._fn
        cond = self._coerce_bool(self.visit(node.cond))
        ok_b   = fn.append_basic_block('assert_ok')
        fail_b = fn.append_basic_block('assert_fail')
        self._builder.cbranch(cond, ok_b, fail_b)
        self._builder.position_at_end(fail_b)
        self._builder.call(self._fn_printf,
                           [self._str_const("assertion failed\n")])
        self._builder.unreachable()
        self._builder.position_at_end(ok_b)

    # ────────────────────────────────────────────────────────────────
    # Expressions
    # ────────────────────────────────────────────────────────────────

    # ── Literals ─────────────────────────────────────────────────────

    def visit_IntLit(self, n): return ir.Constant(i64, n.value)
    def visit_FloatLit(self, n): return ir.Constant(dbl, n.value)
    def visit_BoolLit(self, n): return ir.Constant(i1, int(n.value))
    def visit_NullLit(self, n): return NULL
    def visit_StrLit(self, n):
        """
        String literal in value context → ARC-managed string object.
        The raw global constant is still used internally by _str_const
        for format strings passed to printf.
        """
        raw = self._str_const(n.value)                         # global i8*
        length = ir.Constant(i64, len(n.value.encode('utf-8')))
        return self._builder.call(self._fn_str_new, [raw, length], name='str')

    def visit_InterpolatedStr(self, node: InterpolatedStr) -> ir.Value:
        for p in node.parts:
            if isinstance(p, StrLit): return self._str_const(p.value)
        return self._str_const("")

    # ── Identifier ───────────────────────────────────────────────────

    def visit_Ident(self, node: Ident) -> ir.Value:
        slot = self._lookup(node.name)
        if slot is None:
            raise CodegenError(f"undefined: '{node.name}'", node)
        val = self._builder.load(slot.ptr, name=node.name)
        # Auto-call nullary functions: if the loaded value is a function pointer
        # with no parameters, call it immediately so  pi  = pi()  in expressions.
        ty = val.type
        if (isinstance(ty, ir.PointerType)
                and isinstance(ty.pointee, ir.FunctionType)
                and len(ty.pointee.args) == 0
                and not isinstance(ty.pointee.return_type, ir.VoidType)):
            val = self._builder.call(val, [], name=f'{node.name}_val')
        return val

    def visit_SelfExpr(self, node: SelfExpr) -> ir.Value:
        slot = self._lookup('self')
        if slot is None: raise CodegenError("'self' outside method", node)
        return self._builder.load(slot.ptr, name='self')

    def visit_ClassRef(self, node: ClassRef) -> ir.Value:
        return NULL  # used as constructor target — resolved in visit_Call

    # ── Field / property access ───────────────────────────────────────

    def _gep_field(self, bld: ir.IRBuilder, raw: ir.Value,
                   ci: ClassInfo, field_name: str) -> ir.Value:
        """Return GEP pointer to field (not loaded)."""
        typed = bld.bitcast(raw, ci.struct_ty.as_pointer())
        fi    = ci.struct_field_index(field_name)
        return bld.gep(typed,
                       [ir.Constant(i32, 0), ir.Constant(i32, fi)],
                       inbounds=True)

    def _resolve_obj_class(self, node) -> Optional[ClassInfo]:
        """
        Try to determine the ClassInfo for an object expression.
        For now: if the expression is an Ident whose slot type is i8*
        and the name is a known class, return that class.
        Falls back to None (dynamic dispatch via runtime vtable).
        """
        if isinstance(node, Ident):
            # If there's a class with the variable's declared type, use it.
            # Heuristic: check if variable name matches a field in current class
            # or if the AST node has type annotation info.
            pass
        return None

    def visit_PropAccess(self, node: PropAccess) -> ir.Value:
        obj = self.visit(node.obj)

        # If we're in a class and prop matches a field, use GEP
        if self._current_class and node.prop in self._current_class.field_index:
            ci   = self._current_class
            ptr  = self._gep_field(self._builder, obj, ci, node.prop)
            return self._builder.load(ptr, name=node.prop)

        # Try known classes by scanning
        for ci in self._classes.values():
            if node.prop in ci.field_index:
                ptr = self._gep_field(self._builder, obj, ci, node.prop)
                return self._builder.load(ptr, name=node.prop)

        return NULL

    def visit_SafePropAccess(self, node: SafePropAccess) -> ir.Value:
        obj    = self.visit(node.obj)
        is_ok  = self._builder.icmp_unsigned('!=', obj, NULL)
        fn_    = self._fn
        ok_b   = fn_.append_basic_block('safe_prop_ok')
        end_b  = fn_.append_basic_block('safe_prop_end')
        self._builder.cbranch(is_ok, ok_b, end_b)

        self._builder.position_at_end(ok_b)
        val = NULL
        if self._current_class and node.prop in self._current_class.field_index:
            ci  = self._current_class
            ptr = self._gep_field(self._builder, obj, ci, node.prop)
            val = self._builder.load(ptr)
        ok_b2 = self._builder.block
        self._builder.branch(end_b)

        self._builder.position_at_end(end_b)
        phi = self._builder.phi(i8p)
        phi.add_incoming(NULL, self._builder.block)  # from null guard
        phi.add_incoming(val,  ok_b2)
        return phi

    # ── Method dispatch ───────────────────────────────────────────────

    def _vtable_dispatch(self, obj: ir.Value, method_name: str,
                         args: list[ir.Value]) -> ir.Value:
        """
        Emit a vtable-based virtual method call.
        Load vtable ptr from object header field 0, GEP to method slot,
        bitcast to the correct function pointer type, call.
        """
        bld = self._builder

        # Cast object to header struct to get vtable field
        hdr_typed  = bld.bitcast(obj, HEADER_T.as_pointer())
        vt_slot    = bld.gep(hdr_typed,
                             [ir.Constant(i32, 0), ir.Constant(i32, 0)],
                             inbounds=True,
                             name='vt_slot')
        vtable_raw = bld.load(vt_slot, name='vtable')

        # We need the vtable type and slot index.
        # Search all registered classes for one that has this method.
        ci   = None
        slot = None
        for c in self._classes.values():
            if method_name in c.method_slot:
                ci   = c
                slot = c.method_slot[method_name]
                break

        if ci is None:
            # Unknown method — can't dispatch
            return NULL

        # GEP to the method slot: vtable field index = 4 + slot
        vtable_typed = bld.bitcast(vtable_raw, ci.vtable_ty.as_pointer())
        method_gep   = bld.gep(vtable_typed,
                                [ir.Constant(i32, 0),
                                 ir.Constant(i32, 4 + slot)],
                                inbounds=True)
        method_raw   = bld.load(method_gep, name=f'{method_name}_raw')

        # Bitcast to actual function type
        fn_ty   = ci.method_fnty[method_name]
        method  = bld.bitcast(method_raw, fn_ty.as_pointer())
        return bld.call(method, [obj] + args, name=f'{method_name}_ret')

    def visit_MethodCall(self, node: MethodCall) -> ir.Value:
        # ── Namespace-qualified call:  ns::fn:args ────────────────────
        # geometry::dot:1,2  →  lookup "geometry::dot" in scope, call it
        if isinstance(node.obj, Ident) and node.obj.name in self._namespaces:
            ns   = node.obj.name
            qual = f'{ns}::{node.method}'
            slot = self._lookup(qual)
            if slot is not None:
                args = [self.visit(a) for a in node.args]
                fn_ptr = self._builder.load(slot.ptr, name=qual)
                return self._builder.call(fn_ptr, args, name=f'{node.method}_ret')
            raise CodegenError(
                f"'{node.method}' not found in namespace '{ns}'", node)

        obj  = self.visit(node.obj)
        args = [self.visit(a) for a in node.args]

        # ── String built-in methods ──────────────────────────────────
        m = node.method
        def a(i=0): return args[i] if i < len(args) else ir.Constant(i64, 0)
        def ai(i=0):
            v = a(i)
            if isinstance(v.type, ir.IntType) and v.type.width < 64:
                return self._builder.sext(v, i64)
            return v

        if m == 'to_upper':   return self._builder.call(self._fn_str_to_upper,   [obj])
        if m == 'to_lower':   return self._builder.call(self._fn_str_to_lower,   [obj])
        if m == 'trim':       return self._builder.call(self._fn_str_trim,        [obj])
        if m == 'trim_start': return self._builder.call(self._fn_str_trim_start,  [obj])
        if m == 'trim_end':   return self._builder.call(self._fn_str_trim_end,    [obj])
        # String-only methods: only fire when arg[0] is a pointer (string), not int
        def _is_str_arg(v): return isinstance(v.type, ir.PointerType)
        if m == 'contains' and args:
            if _is_str_arg(a(0)):
                return self._builder.call(self._fn_str_contains,   [obj, a(0)])
        if m == 'starts_with' and args:
            return self._builder.call(self._fn_str_starts_with,[obj, a(0)])
        if m == 'ends_with' and args:
            return self._builder.call(self._fn_str_ends_with,  [obj, a(0)])
        if m == 'index_of' and args:
            if _is_str_arg(a(0)):
                return self._builder.call(self._fn_str_index_of,   [obj, a(0)])
        if m == 'slice' and len(args) >= 2 and _is_str_arg(obj):
            return self._builder.call(self._fn_str_slice, [obj, ai(0), ai(1)])
        if m == 'replace' and len(args) >= 2:
            return self._builder.call(self._fn_str_replace, [obj, a(0), a(1)])
        if m == 'repeat' and args:
            return self._builder.call(self._fn_str_repeat, [obj, ai(0)])
        if m == 'to_int':   return self._builder.call(self._fn_str_to_int,   [obj])
        if m == 'to_float': return self._builder.call(self._fn_str_to_float, [obj])

        # ── Array built-in methods ───────────────────────────────────
        # arr::push:val  arr::pop  arr::get:i  arr::set:i,val
        m = node.method
        if m == 'push' and args:
            val = args[0]
            if isinstance(val.type, ir.IntType) and val.type.width < 64:
                val = self._builder.sext(val, i64)
            elif isinstance(val.type, ir.PointerType):
                val = self._builder.ptrtoint(val, i64)
            self._builder.call(self._fn_arr_push, [obj, val])
            return ir.Constant(i64, 0)
        if m == 'pop':
            return self._builder.call(self._fn_arr_pop, [obj], name='pop')
        if m == 'get' and args:
            idx = args[0]
            if isinstance(idx.type, ir.IntType) and idx.type.width < 64:
                idx = self._builder.sext(idx, i64)
            return self._builder.call(self._fn_arr_get, [obj, idx], name='get')
        if m == 'set' and len(args) >= 2:
            idx = args[0]
            val = args[1]
            if isinstance(idx.type, ir.IntType) and idx.type.width < 64:
                idx = self._builder.sext(idx, i64)
            if isinstance(val.type, ir.IntType) and val.type.width < 64:
                val = self._builder.sext(val, i64)
            self._builder.call(self._fn_arr_set, [obj, idx, val])
            return ir.Constant(i64, 0)
        if m == 'len':
            return self._builder.call(self._fn_arr_len, [obj], name='len')
        if m == 'sort':
            self._builder.call(self._fn_arr_sort, [obj])
            return ir.Constant(i64, 0)
        if m == 'reverse':
            self._builder.call(self._fn_arr_reverse, [obj])
            return ir.Constant(i64, 0)
        if m == 'slice' and len(args) >= 2:
            return self._builder.call(self._fn_arr_slice, [obj, ai(0), ai(1)])
        if m == 'contains' and args:
            return self._builder.call(self._fn_arr_contains, [obj, ai(0)])
        if m == 'index_of' and args:
            return self._builder.call(self._fn_arr_indexof, [obj, ai(0)])
        if m == 'concat' and args:
            return self._builder.call(self._fn_arr_concat, [obj, a(0)])

        # Direct call if class is known
        if self._current_class and node.method in self._current_class.method_fn:
            fn = self._current_class.method_fn[node.method]
            return self._builder.call(fn, [obj] + args)

        # Try direct call via module globals
        fn_name = f'{self._current_class.name}_{node.method}' \
                  if self._current_class else node.method
        if fn_name in self.module.globals:
            return self._builder.call(self.module.globals[fn_name],
                                      [obj] + args)

        # Fall back to vtable dispatch
        return self._vtable_dispatch(obj, node.method, args)

    def visit_SafeMethodCall(self, node: SafeMethodCall) -> ir.Value:
        obj    = self.visit(node.obj)
        is_ok  = self._builder.icmp_unsigned('!=', obj, NULL)
        fn_    = self._fn
        ok_b   = fn_.append_basic_block('safe_meth_ok')
        end_b  = fn_.append_basic_block('safe_meth_end')
        self._builder.cbranch(is_ok, ok_b, end_b)

        self._builder.position_at_end(ok_b)
        args   = [self.visit(a) for a in node.args]
        result = self._vtable_dispatch(obj, node.method, args)
        res_ty = result.type
        ok_b2  = self._builder.block
        self._builder.branch(end_b)

        self._builder.position_at_end(end_b)
        null_res = ir.Constant(res_ty, 0) if not isinstance(res_ty, ir.PointerType) else NULL
        phi = self._builder.phi(res_ty)
        phi.add_incoming(null_res, self._builder.block)
        phi.add_incoming(result,   ok_b2)
        return phi

    # ── Self-call (recursive / field access) ──────────────────────────

    def visit_SelfCall(self, node: SelfCall) -> ir.Value:
        if node.args:
            # Recursive call to current function
            args = [self.visit(a) for a in node.args]
            return self._builder.call(self._fn, args, name='recurse')

        # No args — try field access on 'self' first
        self_slot = self._lookup('self')
        if self_slot is not None and self._current_class is not None:
            ci = self._current_class
            if node.name in ci.field_index:
                raw = self._builder.load(self_slot.ptr, name='self')
                ptr = self._gep_field(self._builder, raw, ci, node.name)
                return self._builder.load(ptr, name=node.name)

        # Fall back to local variable lookup
        slot = self._lookup(node.name)
        if slot is not None:
            return self._builder.load(slot.ptr, name=node.name)

        raise CodegenError(
            f"@:{node.name} — not a field or local. "
            f"For recursion use ::fn_name:args", node)

    # ── Assign ───────────────────────────────────────────────────────

    def visit_Assign(self, node: Assign) -> ir.Value:
        rhs = self.visit(node.value)

        # ── Field assignment: @:field :< val ─────────────────────────
        if isinstance(node.target, SelfCall) and not node.target.args:
            self_slot = self._lookup('self')
            if self_slot is not None and self._current_class is not None:
                ci = self._current_class
                fn_name = node.target.name
                if fn_name in ci.field_index:
                    raw = self._builder.load(self_slot.ptr, name='self')
                    ptr = self._gep_field(self._builder, raw, ci, fn_name)
                    if ci.field_is_obj[fn_name]:
                        # retain new, release old
                        cond = self._builder.icmp_unsigned('!=', rhs, NULL)
                        with self._builder.if_then(cond):
                            self._retain(rhs)
                        old = self._builder.load(ptr)
                        self._builder.store(rhs, ptr)
                        old_cond = self._builder.icmp_unsigned('!=', old, NULL)
                        with self._builder.if_then(old_cond):
                            self._release(old)
                    else:
                        self._builder.store(rhs, ptr)
                    return rhs

        # ── Subscript assignment: arr[i] :< val ──────────────────────
        if isinstance(node.target, Subscript):
            arr = self.visit(node.target.obj)
            idx = self.visit(node.target.index)
            if isinstance(idx.type, ir.IntType) and idx.type.width < 64:
                idx = self._builder.sext(idx, i64)
            val = rhs
            if isinstance(val.type, ir.IntType) and val.type.width < 64:
                val = self._builder.sext(val, i64)
            elif isinstance(val.type, ir.PointerType):
                val = self._builder.ptrtoint(val, i64)
            self._builder.call(self._fn_arr_set, [arr, idx, val])
            return rhs

        # ── Local variable assignment ─────────────────────────────────
        if isinstance(node.target, Ident):
            name = node.target.name
            slot = self._lookup(name)

            if slot is None:
                rhs_ty = rhs.type
                is_obj = isinstance(rhs_ty, ir.PointerType)
                # Track generator type so <<| can find the right GenInfo
                gname = None
                if isinstance(node.value, Call) and isinstance(node.value.fn, Ident):
                    fn_name_v = node.value.fn.name
                    gname = fn_name_v if fn_name_v in self._gens else None
                    # fmt:"..." now returns a proper ARC string (lang_str_new)
                    # so is_obj stays True for proper retain/release
                # String literals now create ARC string objects via lang_str_new
                # (StrLit is ARC, InterpolatedStr TBD)
                if isinstance(node.value, InterpolatedStr):
                    is_obj = False  # interpolated strings not yet ARC-ified
                ptr = self._alloca(rhs_ty, name=name)
                if is_obj:
                    self._retain(rhs)
                    self._builder.store(NULL, ptr)
                self._builder.store(rhs, ptr)
                self._define(name, VarSlot(ptr=ptr, ty=rhs_ty, is_obj=is_obj,
                                           gen_name=gname))
            else:
                if slot.is_obj:
                    cond = self._builder.icmp_unsigned('!=', rhs, NULL)
                    with self._builder.if_then(cond):
                        self._retain(rhs)
                    old = self._builder.load(slot.ptr)
                    self._builder.store(rhs, slot.ptr)
                    old_cond = self._builder.icmp_unsigned('!=', old, NULL)
                    with self._builder.if_then(old_cond):
                        self._release(old)
                else:
                    self._builder.store(rhs, slot.ptr)

        return rhs

    def visit_AugAssign(self, node: AugAssign) -> ir.Value:
        if isinstance(node.target, SelfCall) and not node.target.args \
                and self._current_class:
            # @:field @:< op rhs
            self_slot = self._lookup('self')
            ci = self._current_class
            if self_slot and node.target.name in ci.field_index:
                raw  = self._builder.load(self_slot.ptr)
                ptr  = self._gep_field(self._builder, raw, ci, node.target.name)
                old  = self._builder.load(ptr)
                rhs  = self.visit(node.value)
                new  = self._emit_binop(node.op, old, rhs, node)
                self._builder.store(new, ptr)
                return new

        if not isinstance(node.target, Ident):
            raise CodegenError("augmented assign target must be variable or field", node)
        slot = self._lookup(node.target.name)
        if slot is None:
            raise CodegenError(f"undefined: '{node.target.name}'", node)
        old = self._builder.load(slot.ptr, name=node.target.name)
        rhs = self.visit(node.value)
        new = self._emit_binop(node.op, old, rhs, node)
        self._builder.store(new, slot.ptr)
        return new

    # ── Function call ─────────────────────────────────────────────────

    def _emit_stdlib_call(self, name: str, raw_args: list,
                          node) -> Optional[ir.Value]:
        """
        Dispatch table for stdlib builtins.
        Returns the LLVM value, or None to fall through to user-fn lookup.
        """
        bld  = self._builder
        def ev(i=0): return self.visit(raw_args[i]) if i < len(raw_args) else ir.Constant(i64,0)
        def ev_i64(i=0):
            v = ev(i)
            if isinstance(v.type, ir.IntType) and v.type.width < 64:
                return bld.sext(v, i64)
            if isinstance(v.type, ir.DoubleType):
                return bld.fptosi(v, i64)
            return v
        def ev_dbl(i=0):
            v = ev(i)
            if isinstance(v.type, ir.IntType):
                return bld.sitofp(v, dbl)
            return v

        # ── Math ──────────────────────────────────────────────────────
        if name == 'abs':
            return bld.call(self._fn_abs, [ev_i64(0)])
        if name == 'min':
            a, b = ev(0), ev(1)
            if isinstance(a.type, ir.DoubleType) or isinstance(b.type, ir.DoubleType):
                return bld.call(self._fn_fmin, [ev_dbl(0), ev_dbl(1)])
            return bld.call(self._fn_min, [ev_i64(0), ev_i64(1)])
        if name == 'max':
            a, b = ev(0), ev(1)
            if isinstance(a.type, ir.DoubleType) or isinstance(b.type, ir.DoubleType):
                return bld.call(self._fn_fmax, [ev_dbl(0), ev_dbl(1)])
            return bld.call(self._fn_max, [ev_i64(0), ev_i64(1)])
        if name == 'clamp':
            a = ev(0)
            if isinstance(a.type, ir.DoubleType):
                return bld.call(self._fn_fclamp, [ev_dbl(0), ev_dbl(1), ev_dbl(2)])
            return bld.call(self._fn_clamp, [ev_i64(0), ev_i64(1), ev_i64(2)])
        if name == 'sqrt':  return bld.call(self._fn_sqrt,  [ev_dbl(0)])
        if name == 'floor': return bld.call(self._fn_floor, [ev_dbl(0)])
        if name == 'ceil':  return bld.call(self._fn_ceil,  [ev_dbl(0)])
        if name == 'round': return bld.call(self._fn_round, [ev_dbl(0)])
        if name == 'sin':   return bld.call(self._fn_sin,   [ev_dbl(0)])
        if name == 'cos':   return bld.call(self._fn_cos,   [ev_dbl(0)])
        if name == 'tan':   return bld.call(self._fn_tan,   [ev_dbl(0)])
        if name == 'log':   return bld.call(self._fn_log,   [ev_dbl(0)])
        if name == 'log2':  return bld.call(self._fn_log2,  [ev_dbl(0)])
        if name == 'log10': return bld.call(self._fn_log10, [ev_dbl(0)])
        if name == 'pow':   return bld.call(self._fn_pow,   [ev_dbl(0), ev_dbl(1)])

        # ── Type conversions ──────────────────────────────────────────
        if name == 'int_to_str':   return bld.call(self._fn_int_to_str,   [ev_i64(0)])
        if name == 'float_to_str': return bld.call(self._fn_float_to_str, [ev_dbl(0)])
        if name == 'bool_to_str':  return bld.call(self._fn_bool_to_str,  [ev_i64(0)])
        if name == 'str_to_int':   return bld.call(self._fn_str_to_int,   [ev(0)])
        if name == 'str_to_float': return bld.call(self._fn_str_to_float, [ev(0)])

        # ── I/O ───────────────────────────────────────────────────────
        if name == 'read_line':
            return bld.call(self._fn_read_line, [])
        if name == 'read_file':
            return bld.call(self._fn_read_file, [ev(0)])
        if name == 'write_file':
            bld.call(self._fn_write_file, [ev(0), ev(1)])
            return ir.Constant(i64, 0)
        if name == 'append_file':
            bld.call(self._fn_append_file, [ev(0), ev(1)])
            return ir.Constant(i64, 0)
        if name == 'file_exists':
            return bld.call(self._fn_file_exists, [ev(0)])
        if name == 'print_err':
            v = ev(0)
            if isinstance(v.type, ir.PointerType):
                bld.call(self._fn_print_err, [v])
            return ir.Constant(i64, 0)

        # ── Array standalone ──────────────────────────────────────────
        if name == 'sort':    bld.call(self._fn_arr_sort,    [ev(0)]); return ir.Constant(i64, 0)
        if name == 'reverse': bld.call(self._fn_arr_reverse, [ev(0)]); return ir.Constant(i64, 0)

        return None   # fall through to user function / generator lookup

    def _build_printf_fmt(self, fmt_str: str, rest_ast: list):
        """
        Shared helper: converts a {} format string + arg AST nodes into
        (printf_fmt_str, [promoted_llvm_values]).
        Handles type-based {} → %lld/%g/%s substitution.
        Returns (fmt_str_with_specifiers, [evaluated+promoted values]).
        """
        bld = self._builder

        if '{}' in fmt_str and rest_ast:
            rest_vals = [self.visit(a) for a in rest_ast]
            parts  = fmt_str.split('{}')
            result = parts[0]
            for i, val in enumerate(rest_vals):
                ty = val.type
                if isinstance(ty, ir.IntType):
                    spec = '%lld'
                elif isinstance(ty, ir.DoubleType):
                    spec = '%g'
                else:
                    spec = '%s'
                result += spec
                if i + 1 < len(parts):
                    result += parts[i + 1]
            for extra in parts[len(rest_vals) + 1:]:
                result += extra
        else:
            rest_vals = [self.visit(a) for a in rest_ast]
            result = fmt_str

        promoted = []
        for v in rest_vals:
            ty = v.type
            if isinstance(ty, ir.IntType) and ty.width < 64:
                promoted.append(bld.sext(v, i64))
            elif isinstance(ty, ir.PointerType):
                # Unwrap ARC strings before passing to snprintf/printf
                promoted.append(bld.call(self._fn_any_to_cstr, [v], name='cstr'))
            else:
                promoted.append(v)

        return result, promoted

    def _emit_fmt(self, raw_args: list) -> ir.Value:
        """
        fmt:"hello {}",x,y  →  ARC string with formatted content (no \n).

        Uses snprintf to measure, then lang_str_new to create a proper
        ARC-managed string.  The caller gets a retained i8* that will
        be released when it falls out of scope.
        """
        bld = self._builder

        if not raw_args:
            return bld.call(self._fn_str_new,
                             [self._str_const(''), ir.Constant(i64, 0)], name='str')

        if not isinstance(raw_args[0], StrLit):
            raise CodegenError('fmt: first argument must be a string literal',
                                raw_args[0])

        fmt_str, promoted = self._build_printf_fmt(
            raw_args[0].value, raw_args[1:])

        fmt_ptr  = self._str_const(fmt_str)
        null_buf = ir.Constant(i8p, None)

        # Step 1: measure needed length
        needed   = bld.call(self._fn_snprintf,
                             [null_buf, ir.Constant(i64, 0), fmt_ptr] + promoted,
                             name='needed')
        needed64 = bld.sext(needed, i64)
        size     = bld.add(needed64, ir.Constant(i64, 1), name='bufsize')

        # Step 2: temp buffer, fill, hand off to lang_str_new, free temp
        tmp = bld.call(self._fn_malloc, [size], name='tmp')
        bld.call(self._fn_snprintf, [tmp, size, fmt_ptr] + promoted)
        result = bld.call(self._fn_str_new, [tmp, needed64], name='fmtstr')
        bld.call(self._fn_free, [tmp])

        return result   # ARC i8*

    def _emit_print(self, raw_args: list) -> ir.Value:
        """
        print builtin — three modes:

          print:val
            Auto-detects val type, prints with trailing newline:
              int/bool  →  printf("%lld\n", val)
              float     →  printf("%g\n",   val)
              str/ptr   →  printf("%s\n",   val)

          print:"hello {} is {}",name,val
            First arg is a StrLit containing {} placeholders.
            At codegen time each {} is replaced with the correct printf
            specifier based on the corresponding argument's LLVM type:
              int/bool  →  %lld
              float     →  %g
              str/ptr   →  %s
            Trailing \n is added automatically.

          print:"raw %d fmt",val
            First arg is a StrLit with explicit % specifiers — passed
            verbatim to printf (backward-compat / escape hatch).
        """
        bld = self._builder

        # ── No args → bare newline ────────────────────────────────────
        if not raw_args:
            bld.call(self._fn_printf, [self._str_const('\n')])
            return ir.Constant(i64, 0)

        # ── Format-string mode (first arg is a string literal) ─────────
        if isinstance(raw_args[0], StrLit):
            fmt_str, promoted = self._build_printf_fmt(
                raw_args[0].value, raw_args[1:])
            if not fmt_str.endswith('\n'):
                fmt_str += '\n'
            # Unwrap any ARC string values before passing to printf
            unwrapped = []
            for v in promoted:
                if isinstance(v.type, ir.PointerType):
                    unwrapped.append(bld.call(self._fn_any_to_cstr, [v], name='cstr'))
                else:
                    unwrapped.append(v)
            bld.call(self._fn_printf, [self._str_const(fmt_str)] + unwrapped)
            return ir.Constant(i64, 0)

        # ── Auto-detect single value ──────────────────────────────────
        val = self.visit(raw_args[0])
        ty  = val.type

        if isinstance(ty, ir.IntType):
            if ty.width == 1:
                val = bld.zext(val, i64)
            elif ty.width < 64:
                val = bld.sext(val, i64)
            fmt = self._str_const('%lld\n')
        elif isinstance(ty, ir.DoubleType):
            fmt = self._str_const('%g\n')
        else:
            # i8* — could be ARC string or raw C string.
            # lang_any_to_cstr detects by vtable and returns the right pointer.
            val = bld.call(self._fn_any_to_cstr, [val], name='cstr')
            fmt = self._str_const('%s\n')

        bld.call(self._fn_printf, [fmt, val])
        return ir.Constant(i64, 0)

    def visit_Call(self, node: Call) -> ir.Value:
        # Constructor OR field access — .ClassName:x
        if isinstance(node.fn, ClassRef):
            cname = node.fn.name
            ci    = self._classes.get(cname)
            if ci is not None:
                # Heuristic: if single Ident arg that matches a field name,
                # AND there is a local variable of that class type, it's field
                # access (.Vec2:x in a method body), not a constructor call.
                if (len(node.args) == 1
                        and isinstance(node.args[0], Ident)
                        and node.args[0].name in ci.field_index):
                    field_name = node.args[0].name
                    # Look for a local with the auto-name (vec2, point, …)
                    local_name = cname[0].lower() + cname[1:]
                    slot = self._lookup(local_name)
                    if slot is None and self._current_class == ci:
                        slot = self._lookup('self')
                    if slot is not None:
                        obj = self._builder.load(slot.ptr)
                        ptr = self._gep_field(self._builder, obj, ci, field_name)
                        return self._builder.load(ptr, name=field_name)

                # Constructor call
                args = [self.visit(a) for a in node.args]
                return self._builder.call(ci.constructor, args, name='new_obj')
            return NULL

        # ── print builtin ────────────────────────────────────────────────
        # print: val                 auto-detect type, print + newline
        # print: "fmt %d %s", a, b  printf-style format string + args
        if isinstance(node.fn, Ident) and node.fn.name == 'print':
            return self._emit_print(node.args)

        # ── len builtin ──────────────────────────────────────────────────
        # Dispatches at runtime: checks is_arr vtable flag, else assumes string
        if isinstance(node.fn, Ident) and node.fn.name == 'len':
            if not node.args:
                return ir.Constant(i64, 0)
            val = self.visit(node.args[0])
            fn  = self._fn
            is_arr_blk = fn.append_basic_block('len_arr')
            is_str_blk = fn.append_basic_block('len_str')
            end_blk    = fn.append_basic_block('len_end')
            flag = self._builder.call(self._fn_is_arr, [val], name='is_arr')
            cond = self._builder.icmp_signed('!=', flag, ir.Constant(i32, 0))
            self._builder.cbranch(cond, is_arr_blk, is_str_blk)
            # array path
            self._builder.position_at_end(is_arr_blk)
            arr_len = self._builder.call(self._fn_arr_len, [val], name='alen')
            self._builder.branch(end_blk)
            # string path
            self._builder.position_at_end(is_str_blk)
            str_len = self._builder.call(self._fn_str_len, [val], name='slen')
            self._builder.branch(end_blk)
            # merge
            self._builder.position_at_end(end_blk)
            phi = self._builder.phi(i64, name='len')
            phi.add_incoming(arr_len, is_arr_blk)
            phi.add_incoming(str_len, is_str_blk)
            return phi

        # ── fmt builtin ──────────────────────────────────────────────────
        if isinstance(node.fn, Ident) and node.fn.name == 'fmt':
            return self._emit_fmt(node.args)

        # ── stdlib builtins ───────────────────────────────────────────
        if isinstance(node.fn, Ident):
            result = self._emit_stdlib_call(node.fn.name, node.args, node)
            if result is not None:
                return result

        # ── Field access via typed local: vec2:x  (Ident call, not ClassRef) ──
        # When a class param is auto-named (e.g. .Vec2 param → 'vec2'), the
        # expression  vec2:x  parses as Call(Ident('vec2'), [Ident('x')]).
        # Detect this pattern and convert to a field GEP.
        if (isinstance(node.fn, Ident)
                and len(node.args) == 1
                and isinstance(node.args[0], Ident)):
            local_name = node.fn.name
            field_name = node.args[0].name
            slot = self._lookup(local_name)
            if slot is not None:
                # Find a ClassInfo whose auto-name matches local_name
                for ci_check in self._classes.values():
                    auto = ci_check.name[0].lower() + ci_check.name[1:]
                    if auto == local_name and field_name in ci_check.field_index:
                        obj = self._builder.load(slot.ptr)
                        ptr = self._gep_field(self._builder, obj, ci_check, field_name)
                        return self._builder.load(ptr, name=field_name)

        args = [self.visit(a) for a in node.args]

        fn_name = None
        if isinstance(node.fn, Ident):
            fn_name = node.fn.name

        if fn_name and fn_name in self.module.globals:
            callee = self.module.globals[fn_name]
            if isinstance(callee, ir.Function):
                return self._builder.call(callee, args, name='call')

        # Generator init: 'counter' → 'counter_init'
        if fn_name:
            init_name = f'{fn_name}_init'
            if init_name in self.module.globals:
                callee = self.module.globals[init_name]
                if isinstance(callee, ir.Function):
                    return self._builder.call(callee, args, name='call')

        raise CodegenError(f"unknown function: '{fn_name}'", node)

    # ── Binary / unary ops ────────────────────────────────────────────

    def visit_BinOp(self, node: BinOp) -> ir.Value:
        if node.op == '&': return self._short_circuit(node, True)
        if node.op == '|': return self._short_circuit(node, False)
        return self._emit_binop(node.op,
                                self.visit(node.left),
                                self.visit(node.right), node)

    def _emit_binop(self, op, left, right, node) -> ir.Value:
        lt = left.type; rt = right.type
        # String concatenation: i8* + i8*  →  lang_str_concat
        if (op == '+' and isinstance(lt, ir.PointerType)
                      and isinstance(rt, ir.PointerType)):
            return self._builder.call(self._fn_str_concat, [left, right], name='cat')
        if isinstance(lt, ir.IntType) and isinstance(rt, ir.DoubleType):
            left = self._builder.sitofp(left, dbl); lt = dbl
        if isinstance(rt, ir.IntType) and isinstance(lt, ir.DoubleType):
            right = self._builder.sitofp(right, dbl)
        flt = isinstance(lt, ir.DoubleType)

        if op == '+': return self._builder.fadd(left,right) if flt else self._builder.add(left,right,flags=['nsw'])
        if op == '-': return self._builder.fsub(left,right) if flt else self._builder.sub(left,right,flags=['nsw'])
        if op == '*': return self._builder.fmul(left,right) if flt else self._builder.mul(left,right,flags=['nsw'])
        if op == '/': return self._builder.fdiv(left,right) if flt else self._builder.sdiv(left,right)
        if op == '%': return self._builder.frem(left,right) if flt else self._builder.srem(left,right)
        if op == '**':
            if flt: return self._builder.call(self._fn_sqrt,[self._builder.fmul(left,left)])
            return left  # TODO: int power

        cmp_map = {'==':'==','!=':'!=','<':'<','>':'>','<=':'<=','>=':'>=','?<':'!='}
        if op in cmp_map:
            llop = cmp_map[op]
            if flt: return self._builder.fcmp_ordered(llop,left,right)
            if isinstance(lt, ir.PointerType): return self._builder.icmp_unsigned(llop,left,right)
            return self._builder.icmp_signed(llop,left,right)

        raise CodegenError(f"unknown op: '{op}'", node)

    def _short_circuit(self, node: BinOp, is_and: bool) -> ir.Value:
        fn     = self._fn
        rhs_b  = fn.append_basic_block('sc_rhs')
        end_b  = fn.append_basic_block('sc_end')
        left   = self._coerce_bool(self.visit(node.left))
        lhs_b  = self._builder.block
        self._builder.cbranch(left, rhs_b if is_and else end_b,
                                     end_b if is_and else rhs_b)
        self._builder.position_at_end(rhs_b)
        right  = self._coerce_bool(self.visit(node.right))
        rhs_b2 = self._builder.block
        self._builder.branch(end_b)
        self._builder.position_at_end(end_b)
        phi = self._builder.phi(i1)
        phi.add_incoming(FALSE if is_and else TRUE, lhs_b)
        phi.add_incoming(right, rhs_b2)
        return phi

    def visit_UnaryOp(self, node: UnaryOp) -> ir.Value:
        val = self.visit(node.operand)
        if node.op == '!': return self._builder.not_(self._coerce_bool(val))
        if node.op == '-':
            return self._builder.fneg(val) if isinstance(val.type, ir.DoubleType) \
                   else self._builder.neg(val)
        raise CodegenError(f"unknown unary: '{node.op}'", node)

    # ── Control expressions ───────────────────────────────────────────

    def visit_Ternary(self, node: Ternary) -> ir.Value:
        fn = self._fn
        tb = fn.append_basic_block('tern_then')
        eb = fn.append_basic_block('tern_else')
        mb = fn.append_basic_block('tern_end')
        self._builder.cbranch(self._coerce_bool(self.visit(node.cond)), tb, eb)
        self._builder.position_at_end(tb)
        tv = self.visit(node.then); tb2 = self._builder.block
        self._builder.branch(mb)
        self._builder.position_at_end(eb)
        ev = self.visit(node.else_); eb2 = self._builder.block
        self._builder.branch(mb)
        self._builder.position_at_end(mb)
        phi = self._builder.phi(tv.type)
        phi.add_incoming(tv, tb2); phi.add_incoming(ev, eb2)
        return phi

    def visit_NullCoalesce(self, node: NullCoalesce) -> ir.Value:
        fn = self._fn
        lv = self.visit(node.left); lb = self._builder.block
        rb = fn.append_basic_block('coal_rhs')
        mb = fn.append_basic_block('coal_end')
        nul = self._builder.icmp_unsigned('==', lv, NULL) \
              if isinstance(lv.type, ir.PointerType) \
              else self._builder.icmp_signed('==', lv, ir.Constant(lv.type,0))
        self._builder.cbranch(nul, rb, mb)
        self._builder.position_at_end(rb)
        rv = self.visit(node.right); rb2 = self._builder.block
        self._builder.branch(mb)
        self._builder.position_at_end(mb)
        phi = self._builder.phi(lv.type)
        phi.add_incoming(lv, lb); phi.add_incoming(rv, rb2)
        return phi

    def visit_Pipe(self, node: Pipe) -> ir.Value:
        lv = self.visit(node.left)
        if isinstance(node.right, Ident):
            fake = Call(fn=node.right, args=[node.left],
                        line=node.line, col=node.col)
            return self.visit(fake)
        return lv

    # ── ARC / async stubs ─────────────────────────────────────────────

    def visit_WeakRefExpr(self, n): return self._builder.call(self._fn_weak_ref,[self.visit(n.value)])
    def visit_YieldExpr(self, node: YieldExpr) -> ir.Value:
        """
        ->| val  inside a generator body:
          1. Evaluate val
          2. Call lang_gen_yield(gen_raw, val) — suspends until resumed
          3. Return the send value (what <<| sent in)
        """
        gi = self._current_gen
        if gi is None:
            return self.visit(node.value) if node.value else ir.Constant(i64, 0)

        yv      = self.visit(node.value) if node.value else ir.Constant(gi.yield_ty, 0)
        gen_raw = self._builder.load(self._gen_wrapper_slot, name='gen_raw')
        recv    = self._builder.call(self._fn_gen_yield, [gen_raw, yv], name='recv')
        return recv  # ->| evaluates to the value sent by the caller
    def visit_YieldFromExpr(self, n): return self.visit(n.value)
    def visit_AwaitExpr(self, n):   return self.visit(n.value)
    def visit_GatherExpr(self, n):  return [self.visit(e) for e in n.exprs][-1] if n.exprs else NULL
    def visit_FireForget(self, n):  self.visit(n.value); return NULL
    def visit_RecvChan(self, n):    return ir.Constant(i64,0)
    def visit_SendGen(self, node: SendGen) -> ir.Value:
        """
        g <<| val
          1. If done → return 0
          2. saved_yv = wrapper->yield_val  (set by the last lang_gen_yield)
          3. Call lang_gen_resume(gen_raw, val)  — resumes until next yield/done
          4. Check done flag (set by trampoline if body returned)
          5. Return saved_yv
        """
        gen_wrapper = self.visit(node.gen)
        send_val    = self.visit(node.value)
        fn = self._fn

        # Resolve GenInfo
        gi = None
        if isinstance(node.gen, Ident):
            slot = self._lookup(node.gen.name)
            if slot and slot.gen_name:
                gi = self._gens.get(slot.gen_name)
        if gi is None:
            gi = next(iter(self._gens.values()), None)
        if gi is None:
            return ir.Constant(i64, 0)

        typed_w = self._builder.bitcast(gen_wrapper, gi.wrapper_ty.as_pointer())

        # Check done
        done_ptr     = self._builder.gep(typed_w,
                           [ir.Constant(i32,0), ir.Constant(i32, GEN_DONE_IDX)],
                           inbounds=True)
        already_done = self._builder.icmp_signed('!=',
                           self._builder.load(done_ptr), ir.Constant(i32, 0))

        live_blk = fn.append_basic_block('gen_live')
        dead_blk = fn.append_basic_block('gen_dead')
        end_blk  = fn.append_basic_block('gen_end')
        self._builder.cbranch(already_done, dead_blk, live_blk)

        # ── Dead path ─────────────────────────────────────────────────
        self._builder.position_at_end(dead_blk)
        self._builder.branch(end_blk)

        # ── Live path ─────────────────────────────────────────────────
        self._builder.position_at_end(live_blk)

        # 2. Snapshot current yield_val (from the last yield)
        yv_ptr   = self._builder.gep(typed_w,
                       [ir.Constant(i32,0), ir.Constant(i32, GEN_YIELD_IDX)],
                       inbounds=True)
        saved_yv = self._builder.load(yv_ptr, name='saved_yv')

        # 3. Resume — lang_gen_resume stores send_val + swaps to body
        sv = send_val if send_val.type == gi.send_ty else ir.Constant(gi.send_ty, 0)
        self._builder.call(self._fn_gen_resume, [gen_wrapper, sv])

        # (done flag already set by trampoline if body finished)
        live_end = self._builder.block
        self._builder.branch(end_blk)

        # ── Merge ─────────────────────────────────────────────────────
        self._builder.position_at_end(end_blk)
        phi = self._builder.phi(gi.yield_ty, name='result')
        phi.add_incoming(ir.Constant(gi.yield_ty, 0), dead_blk)
        phi.add_incoming(saved_yv, live_end)
        return phi

    def visit_ThrowGen(self, n):    return NULL
    def visit_NotNullExpr(self, n):
        v = self.visit(n.value)
        if isinstance(v.type, ir.PointerType):
            cond = self._builder.icmp_unsigned('!=', v, NULL)
            with self._builder.if_then(self._builder.not_(cond)):
                self._builder.unreachable()
        return v
    def visit_LenExpr(self, n):     self.visit(n.value); return ir.Constant(i64,0)
    def visit_IterExpr(self, n):    return self.visit(n.value)
    def visit_RangeExpr(self, n):   return NULL

    # ── Collection stubs ─────────────────────────────────────────────

    def visit_ListLit(self, n):
        """
        [1, 2, 3]  →  ARC array with elements pushed in order.
        Elements are coerced to i64 (int arrays only for now).
        """
        bld = self._builder
        cap = ir.Constant(i64, max(len(n.items), 4))
        arr = bld.call(self._fn_arr_new, [cap], name='arr')
        for item in n.items:
            val = self.visit(item)
            # Coerce to i64
            if isinstance(val.type, ir.PointerType):
                val = bld.ptrtoint(val, i64)
            elif isinstance(val.type, ir.IntType) and val.type.width < 64:
                val = bld.sext(val, i64)
            elif isinstance(val.type, ir.DoubleType):
                val = bld.fptosi(val, i64)
            bld.call(self._fn_arr_push, [arr, val])
        return arr
    def visit_DictLit(self, n):
        for k,v in n.pairs: self.visit(k); self.visit(v)
        return NULL
    def visit_SetLit(self, n):
        for i in n.items: self.visit(i)
        return NULL
    def visit_TupleLit(self, n):
        for i in n.items: self.visit(i)
        return NULL
    def visit_Comprehension(self, n):  return NULL
    def visit_Subscript(self, n):
        """arr[i]  →  lang_arr_get(arr, i)"""
        obj = self.visit(n.obj)
        idx = self.visit(n.index)
        if isinstance(idx.type, ir.IntType) and idx.type.width < 64:
            idx = self._builder.sext(idx, i64)
        return self._builder.call(self._fn_arr_get, [obj, idx], name='elem')
    def visit_PartialApp(self, n):     return NULL
    def visit_Lambda(self, n):         return NULL

    # ── Utilities ─────────────────────────────────────────────────────

    def _coerce_bool(self, val: ir.Value) -> ir.Value:
        ty = val.type
        if isinstance(ty, ir.IntType) and ty.width == 1: return val
        if isinstance(ty, ir.IntType):
            return self._builder.icmp_signed('!=', val, ir.Constant(ty, 0))
        if isinstance(ty, ir.DoubleType):
            return self._builder.fcmp_ordered('!=', val, ir.Constant(dbl, 0.0))
        if isinstance(ty, ir.PointerType):
            return self._builder.icmp_unsigned('!=', val, NULL)
        return ir.Constant(i1, 1)

    # ────────────────────────────────────────────────────────────────
    # Code emission
    # ────────────────────────────────────────────────────────────────

    def _patch_coro_ir(self, ir_text: str) -> str:
        """No-op — ucontext generators need no IR patching."""
        return ir_text

    def _run_passes(self, mod_ref):
        """Run CoroSplitPass + O2 optimisations on a parsed module."""
        target = binding.Target.from_default_triple()
        tm     = target.create_target_machine(opt=2, reloc='pic')
        pto    = binding.PipelineTuningOptions()
        pb     = binding.create_pass_builder(tm, pto)
        pm     = pb.getModulePassManager()
        pm.run(mod_ref, pb)
        return tm, mod_ref

    def get_ir(self) -> str:
        """Return LLVM IR text (with presplitcoroutine injected)."""
        return self._patch_coro_ir(str(self.module))

    def compile_to_object(self, path: str):
        ir_text = self._patch_coro_ir(str(self.module))
        mod_ref = binding.parse_assembly(ir_text)
        mod_ref.verify()
        tm, mod_ref = self._run_passes(mod_ref)
        with open(path,'wb') as f:
            f.write(tm.emit_object(mod_ref))
        print(f"written: {path}")

    def compile_and_run(self) -> int:
        ir_text = self._patch_coro_ir(str(self.module))
        mod_ref = binding.parse_assembly(ir_text)
        mod_ref.verify()
        target  = binding.Target.from_default_triple()
        tm      = target.create_target_machine(opt=2)
        pto     = binding.PipelineTuningOptions()
        pb      = binding.create_pass_builder(tm, pto)
        pm      = pb.getModulePassManager()
        pm.run(mod_ref, pb)
        ee      = binding.create_mcjit_compiler(mod_ref, tm)
        import ctypes, os
        # Load ARC runtime BEFORE finalize_object so MCJIT can resolve
        # lang_alloc / lang_retain / lang_release / lang_gen_* symbols
        _arc = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'arc', 'libarc.so')
        if os.path.exists(_arc):
            ctypes.CDLL(_arc, ctypes.RTLD_GLOBAL)
        ee.finalize_object()
        addr = ee.get_function_address('main')
        if not addr: raise CodegenError("no main() defined")
        return ctypes.CFUNCTYPE(ctypes.c_int64)(addr)()


# ════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════

_SMOKE = """\
:add:x_int,y_int[int]
    -> x + y

:factorial:n_int[int]
    ? n <= 1
        -> 1
    -> n * ::factorial:n-1

:sum_range:n_int[int]
    total :< 0
    i     :< 0
    !! i <= n
        total @:< + i
        i     @:< + 1
    -> total

.Point:x_int,y_int
    :str_rep:@[str]
        -> "Point"

    :translate:@,dx_int,dy_int[void]
        @:x @:< + dx
        @:y @:< + dy

:main[int]
    a  :< ::add:3,4
    f5 :< ::factorial:5
    s  :< ::sum_range:100
    p  :< .Point:10,20
    -> 0
"""

def _cmd_bundle(args):
    """sl bundle src.sl [-o name.slb]  — package source into a .slb bundle."""
    import zipfile, json, os, re
    src_path = args.file
    if not src_path:
        print('usage: sl bundle <file.sl> [-o output.slb]'); return

    src = open(src_path, encoding='utf-8').read()
    # Infer bundle name from filename
    base = os.path.splitext(os.path.basename(src_path))[0]
    out  = args.o or f'{base}.slb'

    # Extract namespace names for the manifest
    namespaces = re.findall(r'^~\[(\w+)\]', src, re.MULTILINE)

    manifest = {
        'name':       base,
        'version':    '0.1.0',
        'namespaces': namespaces,
        'entry':      f'src/{os.path.basename(src_path)}',
    }

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest, indent=2))
        zf.write(src_path, f'src/{os.path.basename(src_path)}')

    print(f'bundled: {out}  (namespaces: {", ".join(namespaces) or "none"})')


def _cmd_list(args):
    """sl list  — show all installed packages."""
    import os, json, zipfile
    pkg_dir = os.path.expanduser('~/.sl/packages')
    if not os.path.exists(pkg_dir):
        print('No packages installed.')
        return

    # Find installed packages — look for top-level .sl files
    pkgs = sorted(
        f[:-3] for f in os.listdir(pkg_dir) if f.endswith('.sl')
    )
    if not pkgs:
        print('No packages installed.')
        return

    print(f'Installed packages  ({pkg_dir})')
    print('-' * 50)
    for name in pkgs:
        # Try to read version from manifest inside the bundle dir
        ver = '?'
        manifest_path = os.path.join(pkg_dir, name, 'manifest.json')
        if os.path.exists(manifest_path):
            try:
                info = json.loads(open(manifest_path).read())
                ver  = info.get('version', '?')
            except Exception:
                pass
        print(f'  {name:<25} {ver}')


def _cmd_uninstall(args):
    """sl uninstall <pkg>   or   sl uninstall --self"""
    import os, shutil

    # ── Self-uninstall ─────────────────────────────────────────────────
    if getattr(args, 'self_flag', False):
        sl_bin   = os.path.expanduser('~/.local/bin/sl')
        # Resolve the venv path from inside the wrapper script
        venv_dir = None
        if os.path.exists(sl_bin):
            for line in open(sl_bin):
                if '.venv/bin/python' in line:
                    venv_dir = line.split('"')[1].replace('/.venv/bin/python','/.venv')                                    if '"' in line else None
                    break

        print('This will remove:')
        if os.path.exists(sl_bin):
            print(f'  {sl_bin}  (sl command wrapper)')
        if venv_dir and os.path.exists(venv_dir):
            print(f'  {venv_dir}  (Python virtual environment)')

        pkg_dir = os.path.expanduser('~/.sl/packages')
        remove_pkgs = False
        if os.path.exists(pkg_dir):
            pkgs = [f for f in os.listdir(pkg_dir) if f.endswith('.sl')]
            if pkgs:
                ans = input(f'\nAlso remove {len(pkgs)} installed package(s) in {pkg_dir}? [y/N] ')
                remove_pkgs = ans.strip().lower() == 'y'

        ans = input('\nProceed? [y/N] ')
        if ans.strip().lower() != 'y':
            print('Aborted.')
            return

        if os.path.exists(sl_bin):
            os.remove(sl_bin)
            print(f'Removed {sl_bin}')

        if venv_dir and os.path.exists(venv_dir):
            shutil.rmtree(venv_dir)
            print(f'Removed {venv_dir}')

        if remove_pkgs and os.path.exists(pkg_dir):
            shutil.rmtree(pkg_dir)
            print(f'Removed {pkg_dir}')

        print('\nSL uninstalled. The source files remain — delete the repo folder manually if needed.')
        return

    # ── Package uninstall ─────────────────────────────────────────────
    pkg = getattr(args, 'file', None) or getattr(args, 'pkg', None)
    if not pkg:
        print('usage: sl uninstall <package-name>')
        print('       sl uninstall --self')
        return

    pkg_dir  = os.path.expanduser('~/.sl/packages')
    sl_file  = os.path.join(pkg_dir, f'{pkg}.sl')
    pkg_data = os.path.join(pkg_dir, pkg)

    if not os.path.exists(sl_file) and not os.path.exists(pkg_data):
        print(f"Package '{pkg}' is not installed.")
        print(f"Run  sl list  to see installed packages.")
        return

    print(f"Removing '{pkg}':")
    if os.path.exists(sl_file):  print(f'  {sl_file}')
    if os.path.exists(pkg_data): print(f'  {pkg_data}/')

    ans = input('Proceed? [y/N] ')
    if ans.strip().lower() != 'y':
        print('Aborted.')
        return

    if os.path.exists(sl_file):
        os.remove(sl_file)
    if os.path.exists(pkg_data):
        shutil.rmtree(pkg_data)

    print(f"Uninstalled '{pkg}'.")


REGISTRY_URL = 'https://raw.githubusercontent.com/robruon/sl-registry/main/packages.json'

def _cmd_search(args):
    """sl search [query]  — search the package registry."""
    import urllib.request, json
    query = (args.file or '').lower()
    try:
        with urllib.request.urlopen(REGISTRY_URL, timeout=5) as r:
            packages = json.loads(r.read())
    except Exception as e:
        print(f'Could not fetch registry: {e}')
        print(f'Registry URL: {REGISTRY_URL}')
        return

    matches = {k: v for k, v in packages.items()
               if not query or query in k.lower()
               or query in v.get('description','').lower()}

    if not matches:
        print(f'No packages found for "{query}"')
        return

    print(f'{"Package":<20}  {"Version":<10}  Description')
    print('-' * 60)
    for name, info in sorted(matches.items()):
        ver  = info.get('version', '?')
        desc = info.get('description', '')
        print(f'{name:<20}  {ver:<10}  {desc}')


def _cmd_install(args):
    """sl install bundle.slb  or  sl install https://...  — install a bundle."""
    import zipfile, json, os, shutil, urllib.request
    target = args.file
    if not target:
        print('usage: sl install <bundle.slb or URL>'); return

    pkg_dir = os.path.expanduser('~/.sl/packages')
    os.makedirs(pkg_dir, exist_ok=True)

    # Download if URL
    local_path = target
    if target.startswith('http://') or target.startswith('https://'):
        filename = os.path.join(pkg_dir, os.path.basename(target))
        print(f'downloading {target}...')
        urllib.request.urlretrieve(target, filename)
        local_path = filename
    elif not os.path.exists(target):
        # Try registry lookup for named packages (e.g. sl install geometry)
        print(f'looking up "{target}" in registry...')
        try:
            with urllib.request.urlopen(REGISTRY_URL, timeout=5) as r:
                packages = json.loads(r.read())
            if target not in packages:
                print(f'Package "{target}" not found in registry')
                print(f'Try: sl search {target}')
                return
            url = packages[target]['url']
            filename = os.path.join(pkg_dir, f'{target}.slb')
            print(f'downloading {url}...')
            urllib.request.urlretrieve(url, filename)
            local_path = filename
        except Exception as e:
            print(f'Registry lookup failed: {e}')
            return

    with zipfile.ZipFile(local_path) as zf:
        manifest = json.loads(zf.read('manifest.json'))
        name = manifest['name']
        dest = os.path.join(pkg_dir, name)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)
        for item in zf.namelist():
            if item.startswith('src/'):
                zf.extract(item, dest)
        # Create a top-level .sl file that re-exports from src/
        entry = manifest.get('entry', f'src/{name}.sl')
        entry_abs = os.path.join(dest, entry)
        top_level = os.path.join(pkg_dir, f'{name}.sl')
        if os.path.exists(entry_abs):
            shutil.copy(entry_abs, top_level)

    print(f'installed: {name} → {pkg_dir}/{name}.sl')
    print(f'  namespaces: {", ".join(manifest.get("namespaces", []))}')
    print(f'  use with:   ~> {name}')


def _print_help():
    print("""usage: sl <file.sl> [options]
       sl <command> [args]

compile:
  sl <file.sl>              print LLVM IR
  sl <file.sl> --run        JIT compile and run main()
  sl <file.sl> -o out.o     compile to object file
  sl <file.sl> --ir         print LLVM IR (explicit)

package:
  sl bundle <file.sl>       package source into a .slb bundle
  sl bundle <file.sl> -o x  specify output path
  sl install <pkg>          install bundle (file, URL, or registry name)
  sl uninstall <pkg>        remove an installed package
  sl uninstall --self       remove the sl command and venv
  sl list                   show all installed packages
  sl search [query]         search the package registry

options:
  -h, --help                show this help message
""")


def main():
    import sys

    argv = sys.argv[1:]

    # ── Help ──────────────────────────────────────────────────────────
    if not argv or argv[0] in ('-h', '--help'):
        _print_help()
        return

    # ── Subcommands ───────────────────────────────────────────────────
    if argv[0] == 'bundle':
        import argparse
        ap = argparse.ArgumentParser(prog='sl bundle',
            description='Package a .sl source file into a .slb bundle')
        ap.add_argument('file', metavar='file.sl', help='source file to package')
        ap.add_argument('-o', metavar='OUT', default=None,
                        help='output path (default: <name>.slb)')
        return _cmd_bundle(ap.parse_args(argv[1:]))

    if argv[0] == 'install':
        import argparse
        ap = argparse.ArgumentParser(prog='sl install',
            description='Install a .slb bundle from a file, URL, or registry name')
        ap.add_argument('file', metavar='pkg',
                        help='.slb path, https:// URL, or registered package name')
        return _cmd_install(ap.parse_args(argv[1:]))

    if argv[0] == 'search':
        import argparse
        ap = argparse.ArgumentParser(prog='sl search',
            description='Search the SL package registry')
        ap.add_argument('file', metavar='query', nargs='?',
                        help='search term (omit to list all packages)')
        return _cmd_search(ap.parse_args(argv[1:]))

    if argv[0] == 'list':
        return _cmd_list(None)

    if argv[0] == 'uninstall':
        import argparse
        ap = argparse.ArgumentParser(prog='sl uninstall',
            description='Remove an installed package, or uninstall SL itself')
        ap.add_argument('file', metavar='pkg', nargs='?',
                        help='package name to remove')
        ap.add_argument('--self', dest='self_flag', action='store_true',
                        help='uninstall the sl command and virtual environment')
        return _cmd_uninstall(ap.parse_args(argv[1:]))

    # ── Compile / run ─────────────────────────────────────────────────
    import argparse
    ap = argparse.ArgumentParser(prog='sl', add_help=False)
    ap.add_argument('file', nargs='?')
    ap.add_argument('-o', metavar='OUT')
    ap.add_argument('--ir',  action='store_true')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('-h', '--help', action='store_true')
    args = ap.parse_args(argv)

    if args.help:
        _print_help(); return

    src      = open(args.file, encoding='utf-8').read() if args.file else _SMOKE
    filename = args.file or '<smoke>'

    tokens = lex(src, filename)
    tree   = Parser(tokens, filename).parse()
    cg     = Codegen(filename, source_file=filename)
    cg.visit(tree)

    if args.o:
        cg.compile_to_object(args.o)
        if args.ir: print(cg.get_ir())
    elif args.run:
        exit(cg.compile_and_run())
    else:
        print(cg.get_ir())
if __name__ == '__main__':
    main()
