// A module.register() resolve hook: bare imports that don't resolve where they
// are written resolve from another folder instead, a kernel's runtime folder,
// where its setup installed its packages.
let fallbackParent;

export async function initialize({ parentURL }) {
  fallbackParent = parentURL;
}

export async function resolve(specifier, context, nextResolve) {
  const bare = !/^(?:[./]|[a-z][a-z0-9+.-]*:)/i.test(specifier);
  if (!bare) return nextResolve(specifier, context);
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    if (error?.code !== "ERR_MODULE_NOT_FOUND") throw error;
    return nextResolve(specifier, { ...context, parentURL: fallbackParent });
  }
}
