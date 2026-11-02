import Image from "next/image";

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  return (
    <span className="icon" style={{ width: size, height: size }} aria-hidden="true">
      <Image src={`/icons/${name}.svg`} alt="" width={size} height={size} unoptimized />
    </span>
  );
}
