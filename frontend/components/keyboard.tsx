"use client";

import { useEffect, useState } from "react";

export function KeyboardController() {
  const [help, setHelp] = useState(false);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target?.matches("input,textarea,select,[contenteditable=true]");
      if (event.key === "/" && !typing) {
        event.preventDefault();
        document.querySelector<HTMLInputElement>("[data-global-search]")?.focus();
      } else if (event.key === "?" && !typing) {
        setHelp((value) => !value);
      } else if ((event.key === "j" || event.key === "k") && !typing) {
        window.dispatchEvent(new CustomEvent("pipeline:navigate", { detail: event.key }));
      } else if (event.key === "Escape") {
        setHelp(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  if (!help) return null;
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={() => setHelp(false)}>
      <section className="dialog" role="dialog" aria-modal="true" aria-label="Sneltoetsen" onMouseDown={(event) => event.stopPropagation()}>
        <h2>Sneltoetsen</h2>
        <dl className="shortcut-list"><dt>/</dt><dd>Zoeken</dd><dt>j / k</dt><dd>Volgende / vorige rij</dd><dt>?</dt><dd>Deze hulp</dd><dt>Esc</dt><dd>Sluiten</dd></dl>
      </section>
    </div>
  );
}
