# ─────────────────────────────────────────────────────────────────────
# advanced.sl  —  classes, methods, and class + generator patterns
#
# Run with:   python codegen.py advanced.sl --run
# ─────────────────────────────────────────────────────────────────────


# ── Classes ───────────────────────────────────────────────────────────
# .ClassName:field_type,...
#   Methods inside the body:
#     :method_name:@[return_type]          instance method, no extra params
#     :method_name:@,param_type[return]    instance method with params
#     @:field                              read a field
#     @:field @:< op val                   mutate a field

.Vec2:x_int,y_int

    :dist_sq:@[int]
        -> @:x * @:x + @:y * @:y

    :add:@,.Vec2[void]
        @:x @:< + vec2:x
        @:y @:< + vec2:y

    :scale:@,factor_int[void]
        @:x @:< * factor
        @:y @:< * factor

    :dot:@,.Vec2[int]
        -> @:x * vec2:x + @:y * vec2:y


.Counter:value_int,step_int

    :tick:@[void]
        @:value @:< + @:step

    :reset:@[void]
        @:value @:< * 0

    :get:@[int]
        -> @:value


# ── Constructor hook (:init) ──────────────────────────────────────────
# :init:@[void] runs automatically after fields are set.
# Use it for defaults, validation, or derived values.

.Config:host_str,port_int

    :init:@[void]
        ? @:port == 0
            @:port @:< + 8080

    :url:@[str]
        -> fmt:"{}:{}",@:host,@:port


# ── Generator that walks a counter ───────────────────────────────────

|:counter_gen:start_int,stop_int,step_int[|int]
    i :< start
    !! i < stop
        ->| i
        i @:< + step


# ── Entry point ───────────────────────────────────────────────────────


# ── Namespaces ────────────────────────────────────────────────────────
# Group related code with ~[name].
# Access members with the  ns::name  syntax.

~[stats]
    # Inline function body — single expression, no block needed
    :mean:total_int,count_int[float] -> total * 1.0 / count

    :variance:arr_arr,mean_float[float]
        sum :< 0.0
        i :< 0
        !! i < len:arr
            diff :< arr[i] * 1.0 - mean
            sum @:< + diff * diff
            i @:< + 1
        -> sum / len:arr


:main[int]

    # ── Basic class usage ─────────────────────────────────────────────

    a :< Vec2:3,4
    b :< Vec2:1,2

    print:"a.dist_sq = {}",a::dist_sq
    print:"a dot b   = {}",a::dot:b

    a::scale:2
    print:"a after scale*2: x={} y={}",a::dist_sq,0

    a::add:b
    print:"a after add b: dist_sq={}",a::dist_sq


    # ── Counter class ─────────────────────────────────────────────────

    c :< Counter:0,5
    c::tick
    c::tick
    c::tick
    print:"counter after 3 ticks = {}",c::get
    c::reset
    print:"counter after reset   = {}",c::get


    # ── Generator + class together ────────────────────────────────────
    # Sum even numbers 0,2,4,6,8 using a generator

    gen :< counter_gen:0,10,2
    total :< 0
    n :< 0
    !! n < 5
        v :< gen <<| 0
        total @:< + v
        n @:< + 1
    print:"sum evens 0..8 = {}",total


    # ── Constructor hooks ────────────────────────────────────────
    cfg  :< Config:"localhost",0
    print:"default port = {}",cfg::url
    cfg2 :< Config:"api.example.com",443
    print:"explicit port = {}",cfg2::url


    # ── Namespaces ────────────────────────────────────────────────
    data :< [10, 20, 30, 40, 50]
    total2 :< 0
    k :< 0
    !! k < len:data
        total2 @:< + data[k]
        k @:< + 1
    m :< stats::mean:total2,len:data
    print:"stats::mean = {}",m
    v :< stats::variance:data,m
    print:"stats::variance = {}",v


    -> 0
