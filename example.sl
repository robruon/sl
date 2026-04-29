# ─────────────────────────────────────────────────────────────────────
# example.sl  —  a tour of the language
#
# Run with:   python codegen.py example.sl --run
# Emit IR:    python codegen.py example.sl --ir
# ─────────────────────────────────────────────────────────────────────


# ── Functions ─────────────────────────────────────────────────────────
# :name:param,param[return_type]

:add:a_int,b_int[int]
    -> a + b

:greet:name_str[int]
    print:"hello, {}!",name
    -> 0


# ── Recursion ─────────────────────────────────────────────────────────

:factorial:n_int[int]
    ? n <= 1
        -> 1
    -> n * factorial:n - 1

:fib:n_int[int]
    ? n <= 1
        -> n
    -> fib:n - 1 + fib:n - 2


# ── Generators ────────────────────────────────────────────────────────
# |:name:params[|yield_type]
# ->|  yields a value and suspends
# g <<| val  resumes and returns the previously yielded value

|:range:n_int[|int]
    i :< 0
    !! i < n
        ->| i
        i @:< + 1

|:squares:n_int[|int]
    i :< 1
    !! i <= n
        ->| i * i
        i @:< + 1


# ── Entry point ───────────────────────────────────────────────────────

:main[int]

    # variables:  :< declares,  @:< op rhs mutates
    x :< add:3,4
    print:"3 + 4 = {}",x

    greet:"world"


    # ── if / else-if  (? / ??)  ───────────────────────────────────────

    score :< 85
    ? score >= 90
        print:"grade: A"
    ?? score >= 80
        print:"grade: B"
    ?? score >= 70
        print:"grade: C"


    # ── while loop  !! ───────────────────────────────────────────────

    sum :< 0
    i :< 1
    !! i <= 10
        sum @:< + i
        i @:< + 1
    print:"sum 1..10 = {}",sum


    # ── recursion ─────────────────────────────────────────────────────

    print:"7! = {}",factorial:7
    print:"fib(10) = {}",fib:10


    # ── generators ────────────────────────────────────────────────────

    g :< range:5
    total :< 0
    n :< 0
    !! n < 5
        v :< g <<| 0
        total @:< + v
        n @:< + 1
    print:"sum of range(5) = {}",total

    sq :< squares:4
    a :< sq <<| 0
    b :< sq <<| 0
    c :< sq <<| 0
    d :< sq <<| 0
    print:"squares: {} {} {} {}",a,b,c,d


    # ── string formatting ─────────────────────────────────────────────
    # print:val          auto-detects type, prints with newline
    # print:"{}",val     {} placeholders, types resolved at compile time
    # fmt:"{}",val       same but returns a string instead of printing

    name :< "example"
    version :< 1
    label :< fmt:"{} v{}",name,version
    print:label


    # ── Standard library ──────────────────────────────────────────

    # Math
    print:"abs:-7 = {}",abs:-7
    print:"max:3,9 = {}",max:3,9
    print:"sqrt:144.0 = {}",sqrt:144.0
    print:"pow:2.0,8.0 = {}",pow:2.0,8.0

    # Type conversions
    n :< str_to_int:"42"
    print:"str_to_int = {}",n
    s2 :< int_to_str:n * 2
    print:"int_to_str = {}",s2

    # String methods
    msg :< "  Hello, World!  "
    print:"trim = {}",msg::trim
    print:"upper = {}",msg::trim::to_upper
    print:"contains = {}",msg::contains:"World"
    print:"replace = {}",msg::trim::replace:"World","SL"

    # Array methods
    nums :< [5,3,8,1,9,2,7,4,6]
    nums::sort
    print:"sorted[0] = {}",nums[0]
    print:"sorted[-1] = {}",nums[-1]
    print:"contains 7 = {}",nums::contains:7


    -> 0
