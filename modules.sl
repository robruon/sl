# ─────────────────────────────────────────────────────────────────────
# modules.sl  —  the SL module system
#
# Run with:   python codegen.py modules.sl --run
# ─────────────────────────────────────────────────────────────────────


# ── Importing stdlib ──────────────────────────────────────────────────
# ~> name   finds name.sl in:
#   1. same directory as this file
#   2. ~/.sl/packages/
#   3. <compiler>/stdlib/

~> math
~> strings
~> arrays
~> convert


# ── Defining namespaces ───────────────────────────────────────────────
# ~[name] groups related code. All symbols inside become ns::name.

~[geometry]

    .Vec2:x_int,y_int

        :length:@[float]
            sq :< @:x * @:x + @:y * @:y
            -> math::sqrt:sq * 1.0

        :dot:@,.Vec2[int]
            -> @:x * vec2:x + @:y * vec2:y

    # Inline function body — single expression, no block needed
    :distance:ax_int,ay_int,bx_int,by_int[float]
        dx :< bx - ax
        dy :< by - ay
        -> math::sqrt:dx * dx + dy * dy * 1.0


~[text]

    # Inline one-liners — great for thin wrappers
    :shout:msg_str[str]   -> strings::to_upper:msg
    :whisper:msg_str[str] -> strings::to_lower:msg

    :title_case:msg_str[str]
        # Capitalise first letter — slice + upper + rest
        first :< strings::to_upper:strings::slice:msg,0,1
        rest  :< strings::to_lower:strings::slice:msg,1,strings::len:msg
        -> first + rest


# ── C FFI ─────────────────────────────────────────────────────────────
# Declare any C function and call it directly from SL.
# Type suffixes: _int (i64), _float (f64), _str (i8* ARC), _arr, _void

~[sys]
    ~C :lang_abs:n_int[int]
    ~C :lang_sqrt:x_float[float]

    # Wrap with a clean name
    :abs:n_int[int]     -> lang_abs:n
    :sqrt:x_float[float] -> lang_sqrt:x


# ── Entry point ───────────────────────────────────────────────────────

:main[int]

    # ── stdlib: math ─────────────────────────────────────────────────
    print:"math::pi       = {}",math::pi
    print:"math::sqrt:2.0 = {}",math::sqrt:2.0
    print:"math::pow:2,10 = {}",math::pow:2.0,10.0
    print:"math::sin:0    = {}",math::sin:0.0


    # ── stdlib: strings ──────────────────────────────────────────────
    s :< "  Hello, World!  "
    print:"trim           = {}",strings::trim:s
    trimmed :< strings::trim:s
    print:"upper          = {}",strings::to_upper:trimmed
    print:"contains World = {}",strings::contains:s,"World"
    print:"replace        = {}",strings::replace:trimmed,"World","SL"


    # ── stdlib: arrays ───────────────────────────────────────────────
    nums :< arrays::range:6        # [0, 1, 2, 3, 4, 5]
    print:"range len      = {}",arrays::len:nums
    print:"sum            = {}",arrays::sum:nums

    evens :< []
    i :< 0
    !! i < arrays::len:nums
        ? nums[i] % 2 == 0
            evens::push:nums[i]
        i @:< + 1
    print:"evens          = {}",evens[0]


    # ── stdlib: convert ──────────────────────────────────────────────
    n :< convert::str_to_int:"123"
    print:"str_to_int     = {}",n
    label :< convert::int_to_str:n * 2
    print:"int_to_str     = {}",label


    # ── geometry namespace ───────────────────────────────────────────
    v :< Vec2:3,4
    print:"length         = {}",v::length
    d :< geometry::distance:0,0,3,4
    print:"distance       = {}",d

    w :< Vec2:1,2
    print:"dot product    = {}",v::dot:w


    # ── text namespace ───────────────────────────────────────────────
    print:"shout          = {}",text::shout:"hello world"
    print:"whisper        = {}",text::whisper:"HELLO WORLD"
    print:"title_case     = {}",text::title_case:"hello world"


    # ── sys (FFI) namespace ──────────────────────────────────────────
    print:"sys::abs -5    = {}",sys::abs:-5
    print:"sys::sqrt 16.0 = {}",sys::sqrt:16.0


    -> 0
