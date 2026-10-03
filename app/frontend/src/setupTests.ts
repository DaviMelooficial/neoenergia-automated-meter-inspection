import "@testing-library/jest-dom/vitest";

// jsdom nao implementa matchMedia, e o MantineProvider depende dele para
// resolver o esquema de cores. Sem este mock, todo teste que renderiza a
// arvore com Mantine quebra em "window.matchMedia is not a function".
// Funcao comum, nao vi.fn(): `vi.restoreAllMocks()` nos testes apagaria a
// implementacao de um vi.fn() e o Mantine quebraria no teste seguinte.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

// Mantine tambem usa ResizeObserver em alguns componentes.
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = window.ResizeObserver ?? (ResizeObserverMock as never);

// jsdom nao implementa object URLs; a tela de fila cria e revoga um por foto.
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => `blob:mock-${Math.random().toString(36).slice(2)}`;
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => undefined;
}
