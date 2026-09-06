// Re-export shim: the implementation now lives in ./MessagesApp/index.tsx
// (mirroring the StoreApp/ directory layout). This file keeps the
// `@/apps/MessagesApp` and `./MessagesApp` import paths working for every
// existing call site and test until those can be migrated.
export * from "./MessagesApp/index";
