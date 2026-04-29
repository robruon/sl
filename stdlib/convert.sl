# stdlib/convert.sl  —  Type conversions
# ~> convert
# Access as:  convert::int_to_str, convert::str_to_int, ...

~[convert]
    # ── C bindings ────────────────────────────────────────────────────
    ~C :lang_int_to_str:n_int[str]
    ~C :lang_float_to_str:f_float[str]
    ~C :lang_bool_to_str:b_int[str]
    ~C :lang_str_to_int:s_str[int]
    ~C :lang_str_to_float:s_str[float]

    # ── Clean API ─────────────────────────────────────────────────────

    :int_to_str:n_int[str]     -> lang_int_to_str:n
    :float_to_str:f_float[str] -> lang_float_to_str:f
    :bool_to_str:b_int[str]    -> lang_bool_to_str:b
    :str_to_int:s_str[int]     -> lang_str_to_int:s
    :str_to_float:s_str[float] -> lang_str_to_float:s

    # ── Derived helpers ───────────────────────────────────────────────

    :int_to_bool:n_int[int]
        ? n != 0
            -> 1
        -> 0

    :bool_to_int:b_int[int]
        -> b
