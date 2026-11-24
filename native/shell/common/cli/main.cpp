#include <QCoreApplication>
#include <QTextStream>

#include "themecli.h"

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);
    QTextStream out(stdout), err(stderr);
    const int rc = runThemeCli(app.arguments().mid(1), out, err);
    out.flush();
    err.flush();
    return rc;
}
