/* poc-launch: stamp CLOCK_MONOTONIC into POC_T0_NS, then exec the app. */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: poc-launch <binary> [args...]\n"); return 2; }
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    char buf[32];
    snprintf(buf, sizeof buf, "%lld", (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec);
    setenv("POC_T0_NS", buf, 1);
    execvp(argv[1], argv + 1);
    perror("poc-launch: exec");
    return 127;
}
