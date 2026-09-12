import React from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { Tabs } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import { ChartLineUp, ClockCounterClockwise, UsersThree, Bell } from "phosphor-react-native";
import type { BottomTabBarProps } from "@react-navigation/bottom-tabs";
import { colors, fonts, BORDER, accents } from "@/src/theme";

const TABS: Record<string, { label: string; Icon: any; color: string }> = {
  index: { label: "ANALYZE", Icon: ChartLineUp, color: accents.blue },
  history: { label: "HISTORY", Icon: ClockCounterClockwise, color: accents.pink },
  alerts: { label: "ALERTS", Icon: Bell, color: accents.amber },
  agents: { label: "AGENTS", Icon: UsersThree, color: accents.teal },
};

function BrutalTabBar({ state, navigation }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.bar, { paddingBottom: insets.bottom }]}>
      <View style={styles.row}>
        {state.routes.map((route, index) => {
          const conf = TABS[route.name];
          if (!conf) return null;
          const focused = state.index === index;
          const onPress = () => {
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
            const event = navigation.emit({ type: "tabPress", target: route.key, canPreventDefault: true });
            if (!focused && !event.defaultPrevented) navigation.navigate(route.name);
          };
          const { Icon } = conf;
          const color = focused ? colors.onSurfaceInverse : colors.onSurface;
          return (
            <Pressable
              key={route.key}
              testID={`tab-${route.name}`}
              onPress={onPress}
              style={[
                styles.tab,
                index < state.routes.length - 1 && styles.tabDivider,
                focused && { backgroundColor: conf.color },
              ]}
            >
              <Icon size={22} color={color} weight={focused ? "fill" : "regular"} />
              <Text style={[styles.label, { color }]}>{conf.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

export default function TabsLayout() {
  return (
    <Tabs
      tabBar={(props) => <BrutalTabBar {...props} />}
      screenOptions={{ headerShown: false }}
    >
      <Tabs.Screen name="index" />
      <Tabs.Screen name="history" />
      <Tabs.Screen name="portfolio" options={{ href: null }} />
      <Tabs.Screen name="alerts" />
      <Tabs.Screen name="agents" />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  bar: {
    backgroundColor: colors.surface,
    borderTopWidth: BORDER,
    borderTopColor: colors.borderStrong,
  },
  row: { flexDirection: "row" },
  tab: {
    flex: 1,
    height: 58,
    alignItems: "center",
    justifyContent: "center",
    gap: 3,
    backgroundColor: colors.surface,
  },
  tabActive: { backgroundColor: colors.surfaceInverse },
  tabDivider: { borderRightWidth: BORDER, borderRightColor: colors.borderStrong },
  label: { fontFamily: fonts.monoBold, fontSize: 10, letterSpacing: 1 },
});
