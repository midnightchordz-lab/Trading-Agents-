import React from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { CaretLeft } from "phosphor-react-native";
import { colors, fonts, spacing, BORDER, HEADER_GRADIENT } from "@/src/theme";

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
    <LinearGradient
      colors={HEADER_GRADIENT as unknown as string[]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={[styles.header, { paddingTop: insetsTop + spacing.sm }]}
    >
      <View style={styles.row}>
        {onBack ? (
          <Pressable testID="header-back" onPress={onBack} hitSlop={12} style={styles.back}>
            <CaretLeft size={22} color="#FFFFFF" weight="bold" />
          </Pressable>
        ) : null}
        <View style={{ flex: 1 }}>
          <Text style={styles.title} numberOfLines={1}>
            {title}
          </Text>
          {subtitle ? (
            <Text style={styles.subtitle} numberOfLines={1}>
              {subtitle}
            </Text>
          ) : null}
        </View>
        {right}
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
    borderBottomWidth: BORDER,
    borderBottomColor: colors.borderStrong,
  },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  back: {
    width: 36,
    height: 36,
    borderWidth: BORDER,
    borderColor: "#FFFFFF",
    alignItems: "center",
    justifyContent: "center",
  },
  title: { fontFamily: fonts.display, fontSize: 26, color: "#FFFFFF", letterSpacing: -1 },
  subtitle: { fontFamily: fonts.mono, fontSize: 11, color: "rgba(255,255,255,0.85)", marginTop: 2, letterSpacing: 1 },
});
