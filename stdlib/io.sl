# stdlib/io.sl  —  Input / output
# ~> io
# Access as:  io::read_line, io::write_file, ...

~[io]
    # ── C bindings ────────────────────────────────────────────────────
    ~C :lang_read_line[str]
    ~C :lang_read_file:path_str[str]
    ~C :lang_write_file:path_str,content_str[int]
    ~C :lang_append_file:path_str,content_str[int]
    ~C :lang_file_exists:path_str[int]
    ~C :lang_print_err:msg_str[void]

    # ── Clean API ─────────────────────────────────────────────────────

    :read_line[str]                               -> lang_read_line
    :read_file:path_str[str]                      -> lang_read_file:path
    :write_file:path_str,content_str[int]         -> lang_write_file:path,content
    :append_file:path_str,content_str[int]        -> lang_append_file:path,content
    :file_exists:path_str[int]                    -> lang_file_exists:path
    :print_err:msg_str[void]                      -> lang_print_err:msg

    # ── Derived helpers ───────────────────────────────────────────────

    :print_line:msg_str[void]
        print:msg

    :read_int[int]
        s :< lang_read_line
        -> s::to_int

    :read_float[float]
        s :< lang_read_line
        -> s::to_float
