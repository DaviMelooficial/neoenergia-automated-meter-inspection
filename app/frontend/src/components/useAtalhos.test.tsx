import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useAtalhos, type MapaAtalhos } from "./useAtalhos";

function Componente({ mapa, ativo = true }: { mapa: MapaAtalhos; ativo?: boolean }) {
  useAtalhos(mapa, ativo);
  return (
    <div>
      <input aria-label="campo" />
      <textarea aria-label="texto" />
    </div>
  );
}

describe("useAtalhos", () => {
  it("dispara a acao da tecla, sem distinguir maiuscula", () => {
    const aprovar = vi.fn();
    render(<Componente mapa={{ a: aprovar }} />);
    fireEvent.keyDown(window, { key: "A" });
    fireEvent.keyDown(window, { key: "a" });
    expect(aprovar).toHaveBeenCalledTimes(2);
  });

  it("espaco e digitos funcionam como atalho", () => {
    const pular = vi.fn();
    const motivo = vi.fn();
    render(<Componente mapa={{ " ": pular, "3": motivo }} />);
    fireEvent.keyDown(window, { key: " " });
    fireEvent.keyDown(window, { key: "3" });
    expect(pular).toHaveBeenCalledTimes(1);
    expect(motivo).toHaveBeenCalledTimes(1);
  });

  it("ignora teclas digitadas em campos de texto", () => {
    const aprovar = vi.fn();
    const { getByLabelText } = render(<Componente mapa={{ a: aprovar }} />);
    fireEvent.keyDown(getByLabelText("campo"), { key: "a" });
    fireEvent.keyDown(getByLabelText("texto"), { key: "a" });
    expect(aprovar).not.toHaveBeenCalled();
  });

  it("ignora combinacoes com ctrl/meta e fica inerte quando desativado", () => {
    const aprovar = vi.fn();
    const { rerender } = render(<Componente mapa={{ a: aprovar }} />);
    fireEvent.keyDown(window, { key: "a", ctrlKey: true });
    fireEvent.keyDown(window, { key: "a", metaKey: true });
    expect(aprovar).not.toHaveBeenCalled();
    rerender(<Componente mapa={{ a: aprovar }} ativo={false} />);
    fireEvent.keyDown(window, { key: "a" });
    expect(aprovar).not.toHaveBeenCalled();
  });
});
