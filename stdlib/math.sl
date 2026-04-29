# stdlib/math.sl  —  Math functions
# ~> math
# Access as:  math::abs, math::sqrt, ...

~[math]
    # ── C bindings ────────────────────────────────────────────────────
    ~C :lang_abs:n_int[int]
    ~C :lang_min:a_int,b_int[int]
    ~C :lang_max:a_int,b_int[int]
    ~C :lang_clamp:x_int,lo_int,hi_int[int]
    ~C :lang_fabs:x_float[float]
    ~C :lang_fmin:a_float,b_float[float]
    ~C :lang_fmax:a_float,b_float[float]
    ~C :lang_fclamp:x_float,lo_float,hi_float[float]
    ~C :lang_sqrt:x_float[float]
    ~C :lang_floor:x_float[float]
    ~C :lang_ceil:x_float[float]
    ~C :lang_round:x_float[float]
    ~C :lang_sin:x_float[float]
    ~C :lang_cos:x_float[float]
    ~C :lang_tan:x_float[float]
    ~C :lang_log:x_float[float]
    ~C :lang_log2:x_float[float]
    ~C :lang_log10:x_float[float]
    ~C :lang_pow:base_float,exp_float[float]

    # ── Clean API ─────────────────────────────────────────────────────

    :abs:n_int[int]                          -> lang_abs:n
    :min:a_int,b_int[int]                    -> lang_min:a,b
    :max:a_int,b_int[int]                    -> lang_max:a,b
    :clamp:x_int,lo_int,hi_int[int]          -> lang_clamp:x,lo,hi
    :fabs:x_float[float]                     -> lang_fabs:x
    :fmin:a_float,b_float[float]             -> lang_fmin:a,b
    :fmax:a_float,b_float[float]             -> lang_fmax:a,b
    :fclamp:x_float,lo_float,hi_float[float] -> lang_fclamp:x,lo,hi
    :sqrt:x_float[float]                     -> lang_sqrt:x
    :floor:x_float[float]                    -> lang_floor:x
    :ceil:x_float[float]                     -> lang_ceil:x
    :round:x_float[float]                    -> lang_round:x
    :sin:x_float[float]                      -> lang_sin:x
    :cos:x_float[float]                      -> lang_cos:x
    :tan:x_float[float]                      -> lang_tan:x
    :log:x_float[float]                      -> lang_log:x
    :log2:x_float[float]                     -> lang_log2:x
    :log10:x_float[float]                    -> lang_log10:x
    :pow:base_float,exp_float[float]         -> lang_pow:base,exp

    # ── Constants (zero-arg functions) ────────────────────────────────

    :pi[float]  -> 3.141592653589793
    :tau[float] -> 6.283185307179586
    :e[float]   -> 2.718281828459045
