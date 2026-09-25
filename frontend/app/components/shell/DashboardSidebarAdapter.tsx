"use client";

import Sidebar from "./Sidebar";

type Props = {
  activeKey?: string;
  onSelect?: (key: string) => void;

  /*
   * Dashboard historically passed extra properties to the
   * old Sidebar. Keep adapter tolerant during final cutover.
   */
  [key: string]: unknown;
};

export function DashboardSidebarAdapter({
  activeKey,
  onSelect,
}: Props) {
  return (
    <Sidebar
      active={activeKey}
      onSelect={onSelect}
    />
  );
}

export default DashboardSidebarAdapter;
