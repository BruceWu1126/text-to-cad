import { pluginStarterPrompts } from "@t3tools/client-runtime/state/plugins";
import type { EnvironmentId } from "@t3tools/contracts";
import { useMemo } from "react";

import type { ComposerHandleRef } from "~/composerHandleContext";
import { pluginEnvironment } from "~/state/plugins";
import { useEnvironmentQuery } from "~/state/query";
import { Button } from "../ui/button";

/**
 * What the enabled plugins suggest asking first, under a new thread's
 * headline (text-to-cad: "Design a 3D-printable phone stand…"). A pick goes
 * into the composer to edit before sending.
 */
export function PluginStarterPrompts(props: {
  readonly environmentId: EnvironmentId | null;
  readonly composerRef: ComposerHandleRef;
}) {
  const list = useEnvironmentQuery(
    props.environmentId === null
      ? null
      : pluginEnvironment.list({ environmentId: props.environmentId, input: {} }),
  );
  const prompts = useMemo(() => pluginStarterPrompts(list.data?.plugins ?? [], 3), [list.data]);
  if (prompts.length === 0) return null;
  return (
    <div className="pointer-events-auto mt-4 flex flex-wrap justify-center gap-2">
      {prompts.map((prompt) => (
        <Button
          key={prompt}
          variant="outline"
          size="compact"
          onClick={() => {
            const composer = props.composerRef.current;
            if (!composer) return;
            composer.insertTextAtEnd(prompt, { ensureLeadingBoundary: true });
            composer.focusAtEnd();
          }}
        >
          {prompt}
        </Button>
      ))}
    </div>
  );
}
