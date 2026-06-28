/* An unrelated translation unit that includes no project headers. Listed first
   so it becomes sources[0]: this is what used to make the driver emit a
   transitively-included header's interface from a .i that never saw it. */
int solo(void) {
    return 100;
}
