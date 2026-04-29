# SL — A Symbolic Programming Language

SL is a compiled, statically-typed language with automatic reference counting, generators, classes, and a terse symbolic syntax. It compiles to native machine code via LLVM.

```
:greet:name_str[int]
    print:"hello, {}!",name
    -> 0

:main[int]
    greet:"world"
    -> 0
```

---

## Installation

**Requirements:** Python 3.10+, gcc, pip

```bash
git clone https://github.com/your-org/sl-lang
cd sl-lang

chmod +x install.sh
./install.sh
```

This builds the ARC runtime, installs Python dependencies, and adds the `sl` command to your PATH.

**Verify:**
```bash
sl example.sl --run
```

### VS Code Extension

Download the latest `sl-language-*.vsix` from the [Releases page](https://github.com/robruon/sl/releases) for syntax highlighting, snippets, and autocomplete:

1. Open VS Code
2. Extensions panel → `...` → *Install from VSIX*
3. Select `sl-language-*.vsix`

---

## Language Reference

### Functions

```
:name:param_type,...[return_type]
    body
    -> value
```

- Parameters: `name_type` e.g. `x_int`, `msg_str`, `ratio_float`
- Return type in `[brackets]`. Omit for void.
- `->` returns a value

```
:add:a_int,b_int[int]
    -> a + b

:greet:name_str
    print:"hello, {}!",name
```

### Variables

```
x :< 42          # declare + assign
x @:< + 1        # mutate (augmented assign: @:< op value)
```

`@:<` always requires an operator: `+`, `-`, `*`, `/`, `%`, `**`

### Control Flow

```
? condition       # if
    body
?? condition      # else-if
    body
--                # else
    body
```

```
!! condition      # while
    body
```

### Types

| Type | Suffix | Example |
|------|--------|---------|
| Integer (i64) | `_int` | `x_int` |
| Float (f64) | `_float` | `ratio_float` |
| String | `_str` | `name_str` |
| Boolean | `_bool` | `flag_bool` |

### Strings

```
name :< "world"
greeting :< fmt:"hello, {}",name    # formatted string
combined :< "hello " + name         # concatenation
n :< len:name                       # length
print:name                          # print value
print:"value is {}",name            # print with format
```

`{}` placeholders are resolved at **compile time** — the right printf specifier
is chosen based on the argument's type.

### Arrays

```
nums :< [1, 2, 3, 4, 5]   # create
empty :< []                 # empty array

x :< nums[0]               # get by index (0-based)
x :< nums[-1]              # negative indexing
n :< len:nums               # length

nums[0] :< 99              # set by index
nums::push:42              # append
last :< nums::pop          # remove + return last element

# Iterate
i :< 0
!! i < len:nums
    print:nums[i]
    i @:< + 1
```

### Classes

```
.ClassName:field_type,...
    :method_name:@[return_type]
        -> @:field          # read field with @:

    :method_name:@,param_type[return_type]
        @:field @:< + param  # mutate field
```

```
.Vec2:x_int,y_int
    :length_sq:@[int]
        -> @:x * @:x + @:y * @:y

    :scale:@,factor_int[void]
        @:x @:< * factor
        @:y @:< * factor

# Usage
v :< Vec2:3,4
print:v::length_sq          # 25
v::scale:2
```

### Generators

Generators are functions that yield a sequence of values.

```
|:name:params[|yield_type]
    ->| value               # yield and suspend
    received :< ->| value   # yield and receive sent value
```

```
|:range:n_int[|int]
    i :< 0
    !! i < n
        ->| i
        i @:< + 1

# Usage
g :< range:5
a :< g <<| 0    # resume, get next value (send 0)
b :< g <<| 0    # 0, 1, 2, ...
```

`<<|` resumes the generator and evaluates to the last yielded value. When
the generator is exhausted, `<<|` returns 0.

### Operators

| Operator | Meaning |
|----------|---------|
| `+` `-` `*` `/` `%` `**` | Arithmetic |
| `==` `!=` `<` `>` `<=` `>=` | Comparison |
| `&` `\|` | Logical and / or |
| `\|>` | Pipe: `val \|> fn` = `fn:val` |
| `?:` | Null coalesce |

### Built-in Functions

#### Output
| Function | Description |
|----------|-------------|
| `print:val` | Print any value with newline (auto-detects type) |
| `print:"fmt {}",val,...` | Print with `{}` placeholders |
| `fmt:"fmt {}",val,...` | Return formatted ARC string |
| `print_err:msg` | Print string to stderr |

#### Math
| Function | Description |
|----------|-------------|
| `abs:x` | Absolute value (int) |
| `min:a,b` / `max:a,b` | Min / max (int or float) |
| `clamp:x,lo,hi` | Clamp value to range |
| `sqrt:x` | Square root → float |
| `floor:x` / `ceil:x` / `round:x` | Rounding → float |
| `sin:x` / `cos:x` / `tan:x` | Trig functions |
| `log:x` / `log2:x` / `log10:x` | Logarithms |
| `pow:x,y` | Power |

#### Type Conversions
| Function | Description |
|----------|-------------|
| `int_to_str:n` | Integer → string |
| `float_to_str:f` | Float → string |
| `bool_to_str:b` | Bool → `"true"` or `"false"` |
| `str_to_int:s` | String → integer (0 on failure) |
| `str_to_float:s` | String → float (0.0 on failure) |

#### String Methods (via `::`)
| Method | Description |
|--------|-------------|
| `s::trim` | Strip leading/trailing whitespace |
| `s::trim_start` / `s::trim_end` | Strip one side |
| `s::to_upper` / `s::to_lower` | Case conversion |
| `s::contains:"sub"` | Returns 1 if substring found |
| `s::starts_with:"pre"` | Prefix check |
| `s::ends_with:"suf"` | Suffix check |
| `s::index_of:"sub"` | First index, -1 if not found |
| `s::slice:start,end` | Substring (negative indexing ok) |
| `s::replace:"from","to"` | Replace all occurrences |
| `s::repeat:n` | Repeat string n times |
| `s::to_int` | Parse as integer |
| `s::to_float` | Parse as float |
| `a + b` | String concatenation |
| `len:s` | String length |

#### Array Methods (via `::`)
| Method | Description |
|--------|-------------|
| `arr::push:val` | Append element |
| `arr::pop` | Remove and return last element |
| `arr::get:i` | Get by index |
| `arr::set:i,val` | Set by index |
| `arr::sort` | Sort in-place (ascending) |
| `arr::reverse` | Reverse in-place |
| `arr::slice:start,end` | Sub-array (negative indexing ok) |
| `arr::contains:val` | Returns 1 if value found |
| `arr::index_of:val` | First index, -1 if not found |
| `arr::concat:other` | Return new concatenated array |
| `arr[i]` | Index (negative indexing ok) |
| `arr[i] :< val` | Set by index |
| `len:arr` | Array length |

#### I/O
| Function | Description |
|----------|-------------|
| `read_line` | Read line from stdin → string |
| `read_file:"path"` | Read entire file → string |
| `write_file:"path",s` | Write string to file |
| `append_file:"path",s` | Append string to file |
| `file_exists:"path"` | Returns 1 if file exists |

### Comments

```
# line comment

#|
  block comment
|#
```

---

## Modules & Packages

### Namespaces

Group related code into namespaces with `~[name]`:

```
~[math]
    :pi[float]   -> 3.14159
    :tau[float]  -> 6.28318

    :circle_area:r_int[float]
        -> pi * r * r
```

### Importing

```
~> math                     # import math.sl — access as math::pi
~> math:pi,circle_area      # selective — puts pi and circle_area in local scope
~> math as m                # alias — access as m::pi
```

**Resolution order:**
1. Same directory as the current file
2. `~/.sl/packages/` (installed bundles)
3. `<compiler>/stdlib/` (built-in stdlib)

### Standard library

```
~> math       # math::abs, math::sqrt, math::pi, math::pow, ...
~> strings    # strings::trim, strings::to_upper, strings::contains, ...
~> arrays     # arrays::sort, arrays::sum, arrays::range, ...
~> convert    # convert::str_to_int, convert::int_to_str, ...
~> io         # io::read_line, io::read_file, io::write_file, ...
```

### C FFI

Declare C functions callable from SL with `~C`:

```
~C :lang_abs:n_int[int]        # declare C function
~C :lang_str_trim:s_str[str]   # → callable as lang_abs:n, lang_str_trim:s

# Inline function body (one-liner)
:abs:n_int[int] -> lang_abs:n
```

Type annotations in `~C` declarations: `_int` (i64), `_float` (f64), `_str` (i8* ARC), `_arr` (i8* ARC), `_bool` (i1), `_void`.

### Bundles (`.slb`)

A `.slb` file is a ZIP containing source + manifest. It's both a library and a runnable artifact.

```bash
sl bundle mylib.sl -o mylib.slb    # package source into a bundle
sl install mylib.slb               # install from local file
sl install https://example.com/mylib.slb   # install from URL
sl install mylib                   # install from registry (if registered)
sl search geometry                 # search the package registry
```

Installed packages live in `~/.sl/packages/` and are importable by name.

## Compiler

```bash
sl file.sl            # print LLVM IR
sl file.sl --run      # compile and run (JIT)
sl file.sl -o out.o   # compile to object file
sl file.sl --ir       # print LLVM IR (explicit)
sl bundle file.sl     # create .slb bundle
sl install pkg.slb    # install bundle
sl search query       # search registry
```

---

## Examples

`example.sl` — tour of the language: functions, recursion, loops, generators, strings, formatting.

`advanced.sl` — classes with methods, generators with multi-param init.

---

## Memory Model

SL uses **Automatic Reference Counting (ARC)**. Objects are freed when their
reference count reaches zero. The runtime includes a cycle detector for
reference cycles.

- All heap objects (`strings`, `arrays`, `classes`, `generators`) are ARC-managed
- Function parameters are **borrowed** — the caller retains ownership
- Assignment retains, scope exit releases
- `fmt:"..."` and string literals produce ARC strings
- Arrays own their element buffer; the buffer is freed when the array is released

---

## Project Layout

```
sl-lang/
├── codegen.py              compiler + JIT runner
├── lexer.py                tokeniser
├── parser.py               Pratt parser → AST
├── install.sh              setup script (creates .venv, builds libarc.so, installs sl command)
├── README.md               this file
├── example.sl              introductory tour (functions, loops, generators, strings, arrays)
├── advanced.sl             classes, methods, namespaces, inline functions
├── modules.sl              full module system (imports, ~[ns], ~C FFI, stdlib usage)
├── arc/
│   ├── arc_runtime.c       ARC runtime: ref-counting, strings, arrays, generators, stdlib C layer
│   ├── arc_runtime.h       public C API
│   └── Makefile            build targets:
│                             make shared   → libarc.so  (used by compiler JIT)
│                             make          → debug build + tests (address sanitizer)
│                             make release  → optimised build
│                             make tsan     → thread sanitizer build
│                             make clean    → remove all build artefacts
├── stdlib/
│   ├── math.sl             ~[math]     abs, min, max, sqrt, sin, cos, pow, pi, ...
│   ├── strings.sl          ~[strings]  trim, to_upper, contains, replace, pad_left, ...
│   ├── arrays.sl           ~[arrays]   sort, reverse, sum, min, max, fill, range, ...
│   ├── convert.sl          ~[convert]  int_to_str, str_to_int, float_to_str, ...
│   └── io.sl               ~[io]       read_line, read_file, write_file, file_exists, ...
├── .venv/                  Python virtual environment (created by install.sh, not committed)
└── sl-language-0.3.0.vsix  VS Code extension (syntax highlighting + autocomplete)
```

### Import resolution order

When you write `~> math` the compiler searches:
1. Same directory as your source file — for local project modules
2. `~/.sl/packages/` — bundles installed via `sl install`
3. `<compiler_dir>/stdlib/` — built-in stdlib, always available

This means stdlib works out of the box with no install step, local modules
shadow stdlib names naturally, and installed packages sit in between.
