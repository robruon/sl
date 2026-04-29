# stdlib/strings.sl  —  String operations
# ~> strings

~[strings]
    # ── C bindings ────────────────────────────────────────────────────
    ~C :lang_str_to_upper:s_str[str]
    ~C :lang_str_to_lower:s_str[str]
    ~C :lang_str_trim:s_str[str]
    ~C :lang_str_trim_start:s_str[str]
    ~C :lang_str_trim_end:s_str[str]
    ~C :lang_str_contains:s_str,sub_str[int]
    ~C :lang_str_starts_with:s_str,prefix_str[int]
    ~C :lang_str_ends_with:s_str,suffix_str[int]
    ~C :lang_str_index_of:s_str,sub_str[int]
    ~C :lang_str_slice:s_str,start_int,end_int[str]
    ~C :lang_str_replace:s_str,from_str,to_str[str]
    ~C :lang_str_repeat:s_str,n_int[str]
    ~C :lang_str_concat:a_str,b_str[str]
    ~C :lang_str_len:s_str[int]
    ~C :lang_str_eq:a_str,b_str[int]

    # ── Clean API ─────────────────────────────────────────────────────
    :to_upper:s_str[str]                          -> lang_str_to_upper:s
    :to_lower:s_str[str]                          -> lang_str_to_lower:s
    :trim:s_str[str]                              -> lang_str_trim:s
    :trim_start:s_str[str]                        -> lang_str_trim_start:s
    :trim_end:s_str[str]                          -> lang_str_trim_end:s
    :contains:s_str,sub_str[int]                  -> lang_str_contains:s,sub
    :starts_with:s_str,prefix_str[int]            -> lang_str_starts_with:s,prefix
    :ends_with:s_str,suffix_str[int]              -> lang_str_ends_with:s,suffix
    :index_of:s_str,sub_str[int]                  -> lang_str_index_of:s,sub
    :slice:s_str,start_int,end_int[str]           -> lang_str_slice:s,start,end
    :replace:s_str,from_str,to_str[str]           -> lang_str_replace:s,from,to
    :repeat:s_str,n_int[str]                      -> lang_str_repeat:s,n
    :concat:a_str,b_str[str]                      -> lang_str_concat:a,b
    :len:s_str[int]                               -> lang_str_len:s
    :eq:a_str,b_str[int]                          -> lang_str_eq:a,b

    # ── Derived helpers ───────────────────────────────────────────────
    :is_empty:s_str[int]
        ? lang_str_len:s == 0
            -> 1
        -> 0

    :pad_left:s_str,width_int,ch_str[str]
        n :< width - lang_str_len:s
        ? n <= 0
            -> s
        padding :< lang_str_repeat:ch,n
        -> lang_str_concat:padding,s

    :pad_right:s_str,width_int,ch_str[str]
        n :< width - lang_str_len:s
        ? n <= 0
            -> s
        padding :< lang_str_repeat:ch,n
        -> lang_str_concat:s,padding
