# stdlib/arrays.sl  —  Array operations
# ~> arrays
# Access as:  arrays::sort, arrays::slice, ...
# Or as method calls:  arr::sort,  arr::contains:val

~[arrays]
    # ── C bindings ────────────────────────────────────────────────────
    ~C :lang_arr_new:cap_int[arr]
    ~C :lang_arr_push:arr_arr,val_int[void]
    ~C :lang_arr_pop:arr_arr[int]
    ~C :lang_arr_get:arr_arr,idx_int[int]
    ~C :lang_arr_set:arr_arr,idx_int,val_int[void]
    ~C :lang_arr_len:arr_arr[int]
    ~C :lang_arr_sort:arr_arr[void]
    ~C :lang_arr_reverse:arr_arr[void]
    ~C :lang_arr_slice:arr_arr,start_int,end_int[arr]
    ~C :lang_arr_contains:arr_arr,val_int[int]
    ~C :lang_arr_index_of:arr_arr,val_int[int]
    ~C :lang_arr_concat:a_arr,b_arr[arr]

    # ── Clean API ─────────────────────────────────────────────────────

    :new:cap_int[arr]                             -> lang_arr_new:cap
    :push:arr_arr,val_int[void]                   -> lang_arr_push:arr,val
    :pop:arr_arr[int]                             -> lang_arr_pop:arr
    :get:arr_arr,idx_int[int]                     -> lang_arr_get:arr,idx
    :set:arr_arr,idx_int,val_int[void]            -> lang_arr_set:arr,idx,val
    :len:arr_arr[int]                             -> lang_arr_len:arr
    :sort:arr_arr[void]                           -> lang_arr_sort:arr
    :reverse:arr_arr[void]                        -> lang_arr_reverse:arr
    :slice:arr_arr,start_int,end_int[arr]         -> lang_arr_slice:arr,start,end
    :contains:arr_arr,val_int[int]                -> lang_arr_contains:arr,val
    :index_of:arr_arr,val_int[int]                -> lang_arr_index_of:arr,val
    :concat:a_arr,b_arr[arr]                      -> lang_arr_concat:a,b

    # ── Derived helpers (written in SL) ───────────────────────────────

    :sum:arr_arr[int]
        total :< 0
        i :< 0
        !! i < lang_arr_len:arr
            total @:< + lang_arr_get:arr,i
            i @:< + 1
        -> total

    :min:arr_arr[int]
        ? lang_arr_len:arr == 0
            -> 0
        m :< lang_arr_get:arr,0
        i :< 1
        !! i < lang_arr_len:arr
            v :< lang_arr_get:arr,i
            ? v < m
                m @:< * 0
                m @:< + v
            i @:< + 1
        -> m

    :max:arr_arr[int]
        ? lang_arr_len:arr == 0
            -> 0
        m :< lang_arr_get:arr,0
        i :< 1
        !! i < lang_arr_len:arr
            v :< lang_arr_get:arr,i
            ? v > m
                m @:< * 0
                m @:< + v
            i @:< + 1
        -> m

    :fill:n_int,val_int[arr]
        arr :< lang_arr_new:n
        i :< 0
        !! i < n
            lang_arr_push:arr,val
            i @:< + 1
        -> arr

    :range:n_int[arr]
        arr :< lang_arr_new:n
        i :< 0
        !! i < n
            lang_arr_push:arr,i
            i @:< + 1
        -> arr
