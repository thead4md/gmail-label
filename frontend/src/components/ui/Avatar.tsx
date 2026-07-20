import { avatarColor, initial } from '../../lib/format'

export function Avatar({ sender, size = 36 }: { sender?: string | null; size?: number }) {
  return (
    <div
      className="flex shrink-0 items-center justify-center rounded-full font-bold text-white"
      style={{ width: size, height: size, background: avatarColor(sender), fontSize: size * 0.4 }}
    >
      {initial(sender)}
    </div>
  )
}
