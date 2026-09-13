import React, { useEffect, useRef } from "react";
import { View, Text, Pressable, StyleSheet, Animated } from "react-native";
import { CaretLeft } from "phosphor-react-native";
import { fonts, spacing, TERMINAL } from "@/src/theme";

// Terminal-shell header — dark panel, lime accent, monospace, matching the
// login screen so the rest of the app doesn't feel like a different
// product. Same props as before; only the visual treatment changed.
function PulseDot() {
  const opacity = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.25, duration: 800, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 1, duration: 800, useNativeDriver: true }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return <Animated.View style={[styles.pulseDot, { opacity }]} />;
}

export function ScreenHeader({
  title,
  subtitle,
  right,
  insetsTop = 0,
  onBack,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
  insetsTop?: number;
  onBack?: () => void;
}) {
  return (
    <View style={[styles.header, { paddingTop: insetsTop + spacing.sm }]}>
      <View style={styles.row}>
        {onBack ? (
          <Pressable testID="header-back" onPress={onBack} hitSlop={12} style={styles.back}>
            <CaretLeft size={18} color={TERMINAL.lime} weight="bold" />
          </Pressable>
        ) : null}
        <View style={{ flex: 1 }}>
          <View style={styles.statusRow}>
            <PulseDot />
            <Text style={styles.statusText}>AGENT NETWORK ONLINE</Text>
          </View>
          <Text style={styles.title} numberOfLines={1}>
            {title.toUpperCase()}
          </Text>
          {subtitle ? (
            <Text style={styles.subtitle} numberOfLines={1}>
              {subtitle}
            </Text>
          ) : null}
        </View>
        {right}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    backgroundColor: TERMINAL.panel,
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
    borderBottomWidth: 0.5,
    borderBottomColor: TERMINAL.line,
  },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  back: {
    width: 32,
    height: 32,
    borderRadius: 6,
    borderWidth: 0.5,
    borderColor: TERMINAL.line,
    alignItems: "center",
    justifyContent: "center",
  },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 2 },
  pulseDot: { width: 5, height: 5, borderRadius: 2.5, backgroundColor: TERMINAL.lime },
  statusText: { fontFamily: fonts.mono, fontSize: 8.5, letterSpacing: 1.2, color: TERMINAL.lime },
  title: { fontFamily: fonts.monoBold, fontSize: 18, color: TERMINAL.textBright, letterSpacing: 0.5 },
  subtitle: { fontFamily: fonts.mono, fontSize: 11, color: TERMINAL.textDim, marginTop: 1 },
});
