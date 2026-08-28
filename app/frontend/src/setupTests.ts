import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// jsdom nao implementa matchMedia, e o MantineProvider depende dele para
// resolver o esquema de cores. Sem este mock, todo teste que renderiza a
// arvore com Mantine quebra em "window.matchMedia is not a function".
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mantine tambem usa ResizeObserver em alguns componentes.
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = window.ResizeObserver ?? (ResizeObserverMock as never);
