import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import axios from "axios";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { AuthProvider } from "./auth/AuthContext";

function renderizar(rota: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[rota]}>
          <AuthProvider>
            <App />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("App", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sem sessao restauravel, /fila leva para a tela de login", async () => {
    vi.spyOn(axios, "post").mockRejectedValue(new Error("sem cookie de refresh"));
    renderizar("/fila");
    await waitFor(() => expect(screen.getByText("Inspeção de Fotos de Leitura")).toBeInTheDocument());
    expect(screen.getByLabelText(/e-mail/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /entrar/i })).toBeInTheDocument();
  });

  it("rota desconhecida redireciona para a fila (e dai para o login)", async () => {
    vi.spyOn(axios, "post").mockRejectedValue(new Error("sem cookie de refresh"));
    renderizar("/qualquer-coisa");
    await waitFor(() => expect(screen.getByRole("button", { name: /entrar/i })).toBeInTheDocument());
  });
});
