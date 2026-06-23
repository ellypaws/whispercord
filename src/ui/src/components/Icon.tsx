import { iconPath } from "../../../shared/icons";

interface IconProps {
  name: string;
  className?: string;
}

export function Icon({ name, className = "" }: IconProps) {
  return (
    <svg
      class={`lu ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      dangerouslySetInnerHTML={{ __html: iconPath(name) }}
    />
  );
}
