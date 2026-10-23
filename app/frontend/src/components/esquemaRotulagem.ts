/**
 * Esquema v2 de rotulagem (lab-02/rotulos/esquema.md): definicoes operacionais e
 * teclas, as mesmas do app `rotulador/` do Lab 2. As regras de consistencia sao
 * aplicadas aqui para guiar o rotulador e de novo no servidor, que recusa (422).
 */
import type { Cena, Defeito } from "../api/servicos";

export interface ItemEsquema<T> {
  valor: T;
  rotulo: string;
  tecla: string;
  definicao: string;
}

export const CENAS: ItemEsquema<Cena>[] = [
  {
    valor: "digital",
    rotulo: "Digital",
    tecla: "1",
    definicao:
      "Medidor eletrônico com display (LCD/segmentos), mesmo ilegível, apagado ou parcialmente fora do quadro. O mostrador define a cena: segmentos LCD são digital, qualquer que seja a carcaça.",
  },
  {
    valor: "eletromecanico",
    rotulo: "Eletromecânico",
    tecla: "2",
    definicao:
      "Registrador de roletes (ciclométrico) ou ponteiros/disco giratório. Roletes atrás de um visor moderno continuam eletromecânico.",
  },
  {
    valor: "ocorrencia",
    rotulo: "Ocorrência",
    tecla: "3",
    definicao:
      "Contexto da leitura (caixa, padrão de entrada, fachada, portão, muro, poste), mas nenhum medidor visível. Elemento da residência visitada é ocorrência.",
  },
  {
    valor: "nao_medidor",
    rotulo: "Não medidor",
    tecla: "4",
    definicao:
      "Nada relacionado à medição: chão, céu, pessoa, documento, tela preta, objeto aleatório.",
  },
];

export const DEFEITOS: ItemEsquema<Defeito>[] = [
  {
    valor: "reflexo",
    rotulo: "Reflexo",
    tecla: "q",
    definicao:
      "Brilho sobre o vidro/tampa que cobre pelo menos um dígito ou o número de série. Brilho fora dessas áreas não conta.",
  },
  {
    valor: "fora_de_foco",
    rotulo: "Fora de foco",
    tecla: "w",
    definicao:
      "Teste: dá para contar os segmentos de cada dígito? Se não dá, marcar. Granulação com bordas definidas não conta.",
  },
  {
    valor: "tampa_suja",
    rotulo: "Tampa suja",
    tecla: "e",
    definicao:
      "Opacidade fixa na tampa (manchas, amarelado, riscos). Brilho que muda com o ângulo é reflexo.",
  },
  {
    valor: "enquadramento",
    rotulo: "Enquadramento",
    tecla: "r",
    definicao:
      "Display com menos de 1/10 da largura da foto, cortado, ou inclinado a ponto de não dar para ler em linha.",
  },
  {
    valor: "display_apagado",
    rotulo: "Display apagado",
    tecla: "t",
    definicao:
      "Display visível e sem nenhum segmento aceso. Se não dá para ver o display, a causa é tampa suja ou fora de foco. Implica não legível.",
  },
];

export const LEGIVEL = {
  sim: {
    tecla: "s",
    definicao: "Consegue ler todos os dígitos do registro de consumo, sem chutar nenhum.",
  },
  nao: {
    tecla: "n",
    definicao:
      "Pelo menos um dígito ilegível, ou não há registro visível. Na dúvida, não: um dígito errado é uma fatura errada.",
  },
};

export interface RascunhoRotulo {
  cena: Cena | null;
  defeitos: Defeito[];
  legivel: boolean | null;
}

export const RASCUNHO_VAZIO: RascunhoRotulo = { cena: null, defeitos: [], legivel: null };

/** Legível imposto pela cena ou pelos defeitos; null quando a escolha é do rotulador. */
export function legivelImposto(r: RascunhoRotulo): boolean | null {
  if (r.cena === "nao_medidor" || r.cena === "ocorrencia") return false;
  if (r.defeitos.includes("display_apagado")) return false;
  return null;
}

/** Troca de cena como no app do Lab 2: o valor imposto pela cena anterior é apagado. */
export function escolherCena(r: RascunhoRotulo, cena: Cena): RascunhoRotulo {
  const defeitos = cena === "nao_medidor" ? [] : r.defeitos;
  const anterior = legivelImposto(r);
  const proximo = { cena, defeitos, legivel: anterior === null ? r.legivel : null };
  const imposto = legivelImposto(proximo);
  return { ...proximo, legivel: imposto ?? proximo.legivel };
}

export function alternarDefeito(r: RascunhoRotulo, defeito: Defeito): RascunhoRotulo {
  if (r.cena === "nao_medidor") return r;
  const anterior = legivelImposto(r);
  const defeitos = r.defeitos.includes(defeito)
    ? r.defeitos.filter((d) => d !== defeito)
    : [...r.defeitos, defeito];
  const proximo = { ...r, defeitos, legivel: anterior === null ? r.legivel : null };
  return { ...proximo, legivel: legivelImposto(proximo) ?? proximo.legivel };
}

export function escolherLegivel(r: RascunhoRotulo, legivel: boolean): RascunhoRotulo {
  return legivelImposto(r) === null ? { ...r, legivel } : r;
}

export function completo(r: RascunhoRotulo): boolean {
  return r.cena !== null && r.legivel !== null;
}

// Teto do servidor para `segundos_na_foto`. Acima disso (aba esquecida aberta) o
// tempo nao mede nada: vai nulo em vez de travar a gravacao com 422.
const TETO_SEGUNDOS = 3600;

export function segundosNaFoto(inicioMs: number, agoraMs: number): number | null {
  const segundos = Math.round((agoraMs - inicioMs) / 100) / 10;
  return segundos >= 0 && segundos <= TETO_SEGUNDOS ? segundos : null;
}
