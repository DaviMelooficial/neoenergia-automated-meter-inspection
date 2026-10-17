import { useLayoutEffect } from "react";

export type MapaAtalhos = Record<string, (evento: KeyboardEvent) => void>;

/**
 * Atalhos globais de teclado. Ignora teclas digitadas em campos de texto e
 * combinacoes com Ctrl/Meta/Alt, para nao brigar com o navegador. As chaves do
 * mapa sao `event.key` em minusculas (" " para espaco).
 *
 * `useLayoutEffect`, e nao `useEffect`: o ouvinte do item novo entra no mesmo commit
 * que o desenha. Com `useEffect` (executado depois da pintura) uma tecla apertada logo
 * que o item aparece caia no ouvinte velho ou em nenhum; sob carga isso travava os
 * testes de Fila e Auditoria, e o auditor rapido perderia a tecla do mesmo jeito.
 */
export function useAtalhos(mapa: MapaAtalhos, ativo = true): void {
  useLayoutEffect(() => {
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
