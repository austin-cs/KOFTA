/* Tiny target for the SHS C-path smoke test.
 *
 * The crash is gated behind a magic string ("kofta_magic") passed via the
 * -f option. A blind fuzzer would have to guess the literal; the point of the
 * test is that the LLVM pass records the strcmp's source context to the srcmap
 * and afl-fuzz queries kofta-shs, which (with --mock) echoes the literal back
 * out of the source slice -- so the crash is reachable only if the SHS C seam
 * actually fires. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    const char *fmt = NULL;
    int c;
    char buf[64];

    while ((c = getopt(argc, argv, "f:")) != -1) {
        if (c == 'f') fmt = optarg;
    }

    /* consume stdin so AFL has something to mutate */
    if (fgets(buf, sizeof(buf), stdin) == NULL) buf[0] = '\0';

    if (fmt && strcmp(fmt, "kofta_magic") == 0) {
        abort();
    }
    return 0;
}
