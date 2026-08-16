import React from "react";
import Svg, { Polyline, Line } from "react-native-svg";

type Props = {
  data: number[];
  width?: number;
  height?: number;
  color: string;
  strokeWidth?: number;
  showBaseline?: boolean;
  baselineColor?: string;
};

export function Sparkline({
  data,
  width = 120,
  height = 40,
  color,
  strokeWidth = 2,
  showBaseline = false,
  baselineColor = "#E4E4E7",
}: Props) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);
  const pad = strokeWidth;
  const usable = height - pad * 2;
  const points = data
    .map((d, i) => {
      const x = i * step;
      const y = pad + (usable - ((d - min) / range) * usable);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <Svg width={width} height={height}>
      {showBaseline ? (
        <Line x1={0} y1={height - pad} x2={width} y2={height - pad} stroke={baselineColor} strokeWidth={1} />
      ) : null}
      <Polyline points={points} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinejoin="miter" />
    </Svg>
  );
}
