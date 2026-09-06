/**
 * Barrel export for BrowserApp.
 *
 * The app registry imports `@/apps/BrowserApp` and reads the named
 * `BrowserApp` export. With BrowserApp now a directory rather than
 * a single file, this barrel keeps the import path stable.
 */
export { BrowserApp } from "./BrowserApp";
