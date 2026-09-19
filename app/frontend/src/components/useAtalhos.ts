import { useEffect } from "react";

export type MapaAtalhos = Record<string, (evento: KeyboardEvent) => void>;

/**
 * Atalhos globais de teclado. Ignora teclas digitadas em campos de texto e
 * combinacoes com Ctrl/Meta/Alt, para nao brigar com o navegador. As chaves do
 * mapa sao `event.key` em minusculas (" " para espaco).
 */
export function useAtalhos(mapa: MapaAtalhos, ativo = true): void {
  useEffect(() => {
    if (!ativo) return;
    const ouvinte = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
      const alvo = e.target as HTMLElement | null;
      const tag = alvo?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || alvo?.isContentEditable) {
        return;
      }
      const acao = mapa[e.key.toLowerCase()];
      if (acao) {
        e.preventDefault();
        acao(e);
      }
    };
    window.addEventListener("keydown", ouvinte);
    return () => window.removeEventListener("keydown", ouvinte);
  }, [mapa, ativo]);
}
