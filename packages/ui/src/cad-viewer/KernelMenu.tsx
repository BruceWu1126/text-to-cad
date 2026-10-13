import { ChevronDown } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { CadKernelVariant, CadWorkspaceService } from '@text-to-cad/core/client';
import { Button } from '../primitives/button.jsx';
import { DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from '../primitives/dropdown-menu.jsx';
import { FLOATING_SURFACE_CLASS } from '../lib/floatingSurface.js';
import { cn } from '../lib/utils.js';

// The model's own build is build123d's, on OpenCASCADE; every other kernel is named by its folder.
const KERNEL_NAMES: Record<string, string> = { brep: 'BREP', 'usd-brep': 'NVIDIA usd-brep', sdm: 'Emergent Matter SDM', implicit: 'Implicits', manifold: 'Manifold', three: 'three.js' };
export const kernelName = (kernel: string) => KERNEL_NAMES[kernel] ?? kernel;

/**
 * The builds of the model on screen on other CAD kernels, which the kernels skill saves beside it
 * (`.kernels/<kernel>/`). Read when the file changes and when the page comes back into focus (a
 * kernel may have built meanwhile); empty where the client cannot list them or the model has none.
 */
export function useKernelVariants(client: CadWorkspaceService, file: string): readonly CadKernelVariant[] {
  const [found, setFound] = useState<{ client: CadWorkspaceService | null; file: string; variants: readonly CadKernelVariant[] }>({ client: null, file: '', variants: [] });
  useEffect(() => {
    const list = client.kernelVariants;
    if (!file || !list) return;
    const controller = new AbortController();
    const read = () => { void list.call(client, file, { signal: controller.signal })
      .then(variants => setFound({ client, file, variants }), () => setFound({ client, file, variants: [] })); };
    read();
    window.addEventListener('focus', read);
    return () => { controller.abort(); window.removeEventListener('focus', read); };
  }, [client, file]);
  // Only this client's answer for this file: never another file's, or one a previous client gave.
  return found.client === client && found.file === file ? found.variants : [];
}

/**
 * The Kernel menu in the navbar: which kernel's build of the model is on screen, and the others to
 * switch to. Choosing one shows that file in this view, as the explorer's pick would; nothing is
 * built. Drawn only for a model with another kernel's build.
 */
export function KernelMenu({ variants, onOpen }: { variants: readonly CadKernelVariant[]; onOpen(file: string): void }) {
  const current = variants.find(variant => variant.current);
  if (variants.length < 2 || !current) return null;
  return <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button type="button" variant="ghost" size="xs" aria-label={`Kernel: ${kernelName(current.kernel)}`} data-kernel-menu=""
        className="h-6 gap-1 px-1.5 text-tiny text-muted-foreground hover:text-foreground data-[state=open]:bg-accent data-[state=open]:text-accent-foreground">
        {kernelName(current.kernel)}<ChevronDown className="size-3" aria-hidden="true" />
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end" sideOffset={6} collisionPadding={8} aria-label="Kernel" data-kernel-menu-content=""
      className={cn(FLOATING_SURFACE_CLASS, 'min-w-48 data-[state=closed]:animate-none!')}>
      <DropdownMenuLabel inset={false} className="text-tiny text-muted-foreground">Kernel</DropdownMenuLabel>
      <DropdownMenuRadioGroup value={current.file} onValueChange={(file: string) => { if (file !== current.file) onOpen(file); }}>
        {variants.map(variant => <DropdownMenuRadioItem key={variant.file} value={variant.file} className="text-tiny">
          {kernelName(variant.kernel)}
        </DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>;
}
