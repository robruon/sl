# Makefile  ·  ARC runtime
# ══════════════════════════════════════════════════════════════
#
# Targets
#   make            build debug library + run tests
#   make shared     build libarc.so  (used by the SL compiler JIT)
#   make release    optimised build (-O3, no sanitizers)
#   make asan       address + leak sanitizer build
#   make tsan       thread sanitizer build  (for concurrent use)
#   make clean      remove all build artefacts
#   make stats      run tests and print ARC stats

CC      := gcc
AR      := ar
ARFLAGS := rcs
SRC     := arc_runtime.c
HDR     := arc_runtime.h
TEST    := arc_test.c
LIB     := libarc.a
SHARED  := libarc.so
BIN     := arc_test

# ── Compiler flags ─────────────────────────────────────────────
BASE_FLAGS := \
    -std=c17          \
    -Wall             \
    -Wextra           \
    -Wpedantic        \
    -Wshadow          \
    -Wcast-align      \
    -Wstrict-overflow \
    -fno-common

DEBUG_FLAGS   := $(BASE_FLAGS) -O0 -g3 -DDEBUG
RELEASE_FLAGS := $(BASE_FLAGS) -O2 -DNDEBUG
ASAN_FLAGS    := $(BASE_FLAGS) -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer
TSAN_FLAGS    := $(BASE_FLAGS) -O1 -g -fsanitize=thread

# Default: debug with address sanitizer
CFLAGS ?= $(ASAN_FLAGS)

# ── Build rules ────────────────────────────────────────────────
.PHONY: all shared release asan tsan test clean stats

all: CFLAGS = $(ASAN_FLAGS)
all: $(BIN)
	@echo ""
	@./$(BIN)

# Shared library for the SL compiler JIT
# Uses -O2 (not -O3 / march=native) so the .so is portable
shared: $(SHARED)

$(SHARED): $(SRC) $(HDR)
	$(CC) $(RELEASE_FLAGS) -shared -fPIC -o $@ $(SRC)
	@echo "Built $(SHARED)"

release: CFLAGS = $(RELEASE_FLAGS)
release: $(BIN)
	@echo ""
	@./$(BIN)

asan: CFLAGS = $(ASAN_FLAGS)
asan: $(BIN)
	@./$(BIN)

tsan: CFLAGS = $(TSAN_FLAGS)
tsan: $(BIN)
	@./$(BIN)

# ── Link test binary directly (no separate .a step needed for tests)
$(BIN): $(SRC) $(TEST) $(HDR)
	$(CC) $(CFLAGS) $(SRC) $(TEST) -o $@

# ── Static library (for linking into the language runtime)
$(LIB): arc_runtime.o
	$(AR) $(ARFLAGS) $@ $<

arc_runtime.o: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -c $< -o $@

# ── Stats shortcut
stats: $(BIN)
	@./$(BIN) 2>&1 | tail -15

# ── Clean
clean:
	rm -f $(BIN) $(LIB) $(SHARED) arc_runtime.o
