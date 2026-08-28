import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

vi.mock("axios", () => ({
  default: { get: vi.fn(() => new Promise(() => {})) },
}));

function renderizar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("App", () => {
  it("renderiza o titulo da aplicacao", () => {
    renderizar();
    expect(screen.getByText("Inspeção de Fotos de Leitura")).toBeDefined();
  });
});
