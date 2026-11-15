/* Main executable of "Text to Playlist.app".
 * A real binary (not a script) so macOS ties the Full Disk Access grant to this app alone.
 * It only starts the Python app from the tool's private install folder and waits for it. */
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

int main(void) {
    const char *home = getenv("HOME");
    char python[1024];
    if (!home || snprintf(python, sizeof python, "%s/.local/share/text-to-playlist/venv/bin/python", home) >= (int)sizeof python)
        return 1;
    pid_t pid = fork();
    if (pid < 0) return 1;
    if (pid == 0) {
        execl(python, python, "-m", "text_to_playlist.app", (char *)NULL);
        _exit(127);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
