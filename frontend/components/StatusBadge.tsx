'use client';

const COLORS: Record<string, string> = {
  CREATING: 'bg-neutral-500',
  GENERATING: 'bg-yellow-500',
  BUILDING: 'bg-yellow-500',
  READY: 'bg-green-500',
  FAILED: 'bg-red-500',
};

const LABELS: Record<string, string> = {
  CREATING: 'Creating',
  GENERATING: 'Generating',
  BUILDING: 'Building',
  READY: 'Ready',
  FAILED: 'Error',
};

export default function StatusBadge({ status }: { status: string }) {
  const color = COLORS[status] || 'bg-neutral-500';
  const label = LABELS[status] || status;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-neutral-300">
      <span className={`w-2 h-2 rounded-full ${color} ${status === 'GENERATING' || status === 'BUILDING' ? 'animate-pulse' : ''}`} />
      {label}
    </span>
  );
}
