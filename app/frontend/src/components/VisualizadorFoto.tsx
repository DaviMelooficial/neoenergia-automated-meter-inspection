import { Box, Center, Loader, Text } from "@mantine/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAtalhos } from "./useAtalhos";

interface Props {
  src: string | null;
  carregando?: boolean;
  alt?: string;
  /** Liga as setas e +/- como atalhos globais de zoom e pan. */
  ativo?: boolean;
}

const ZOOM_MIN = 1;
const ZOOM_MAX = 8;
const PASSO_PAN = 40;

/**
 * Zoom por scroll ou +/-, pan por arrasto ou setas. As fotos tem 360x480; sem
 * zoom nao da para ler digito pequeno. Duplo clique ou 0 volta ao inicio.
 * O pan e limitado para a imagem nunca sair inteira de vista.
 */
export function VisualizadorFoto({
  src,
  carregando = false,
  alt = "foto do medidor",
  ativo = true,
}: Props) {
  const [zoom, setZoom] = useState(1);
  const [desloc, setDesloc] = useState({ x: 0, y: 0 });
  const arrasto = useRef<{ x: number; y: number; dx: number; dy: number } | null>(null);
  const caixa = useRef<HTMLDivElement>(null);

  const limitar = useCallback((d: { x: number; y: number }, z: number) => {
    const el = caixa.current;
    if (!el) return d;
    // A imagem escalada pode se deslocar ate metade do seu excesso sobre a caixa.
    const maxX = (el.clientWidth * (z - 1)) / 2;
    const maxY = (el.clientHeight * (z - 1)) / 2;
    return {
      x: Math.max(-maxX, Math.min(maxX, d.x)),
      y: Math.max(-maxY, Math.min(maxY, d.y)),
    };
  }, []);

  const resetar = useCallback(() => {
    setZoom(1);
    setDesloc({ x: 0, y: 0 });
  }, []);

  const ajustarZoom = useCallback(
    (fator: number) => {
      setZoom((z) => {
        const novo = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * fator));
        setDesloc((d) => limitar(d, novo));
        return novo;
      });
    },
    [limitar],
  );

  const mover = useCallback(
    (dx: number, dy: number) => setDesloc((d) => limitar({ x: d.x + dx, y: d.y + dy }, zoom)),
    [limitar, zoom],
  );

  // Nova foto: reseta o enquadramento.
  useEffect(resetar, [src, resetar]);

  // Listener nativo non-passive: so assim preventDefault segura o scroll da pagina.
  useEffect(() => {
    const el = caixa.current;
    if (!el) return;
    const aoRolar = (e: WheelEvent) => {
      e.preventDefault();
      ajustarZoom(e.deltaY < 0 ? 1.15 : 1 / 1.15);
    };
    el.addEventListener("wheel", aoRolar, { passive: false });
    return () => el.removeEventListener("wheel", aoRolar);
  }, [ajustarZoom]);

  const atalhos = useMemo(
    () => ({
      "+": () => ajustarZoom(1.25),
      "=": () => ajustarZoom(1.25),
      "-": () => ajustarZoom(1 / 1.25),
      "0": resetar,
      arrowup: () => mover(0, PASSO_PAN),
      arrowdown: () => mover(0, -PASSO_PAN),
      arrowleft: () => mover(PASSO_PAN, 0),
      arrowright: () => mover(-PASSO_PAN, 0),
    }),
    [ajustarZoom, mover, resetar],
  );
  useAtalhos(atalhos, ativo);

  const iniciarArrasto = (e: React.MouseEvent) => {
    arrasto.current = { x: e.clientX, y: e.clientY, dx: desloc.x, dy: desloc.y };
  };
  const arrastar = (e: React.MouseEvent) => {
    if (!arrasto.current) return;
    setDesloc(
      limitar(
        {
          x: arrasto.current.dx + (e.clientX - arrasto.current.x),
          y: arrasto.current.dy + (e.clientY - arrasto.current.y),
        },
        zoom,
      ),
    );
  };
  const soltar = () => {
    arrasto.current = null;
  };

  return (
    <Box
      ref={caixa}
      tabIndex={0}
      role="img"
      aria-label={alt}
      onMouseDown={iniciarArrasto}
      onMouseMove={arrastar}
      onMouseUp={soltar}
      onMouseLeave={soltar}
      onDoubleClick={resetar}
      data-testid="visualizador"
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        minHeight: 520,
        overflow: "hidden",
        background: "#111",
        borderRadius: 8,
        cursor: zoom > 1 ? "grab" : "zoom-in",
        userSelect: "none",
        outlineOffset: 2,
      }}
    >
      {carregando || !src ? (
        <Center h="100%" mih={520}>
          {carregando ? <Loader color="gray" /> : <Text c="dimmed">sem imagem</Text>}
        </Center>
      ) : (
        <img
          src={src}
          alt=""
          draggable={false}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "contain",
            transform: `translate(${desloc.x}px, ${desloc.y}px) scale(${zoom})`,
            transformOrigin: "center center",
            transition: arrasto.current ? "none" : "transform 60ms linear",
            imageRendering: zoom > 3 ? "pixelated" : "auto",
          }}
        />
      )}
      <Text
        size="xs"
        c="gray.4"
        style={{ position: "absolute", right: 10, bottom: 8, pointerEvents: "none" }}
      >
        {zoom.toFixed(1)}x · scroll ou +/-: zoom · arrastar ou setas: mover · duplo clique ou 0:
        reset
      </Text>
    </Box>
  );
}
