export default function ProgressRing({ value }: { value: number }) {
  const radius = 18;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className="progress-ring" aria-label={`完成 ${value}%`}>
      <svg width="46" height="46" viewBox="0 0 46 46" role="img">
        <circle className="progress-ring__track" cx="23" cy="23" r={radius} fill="none" strokeWidth="4" />
        <circle className="progress-ring__value" cx="23" cy="23" r={radius} fill="none" strokeWidth="4" strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round" transform="rotate(-90 23 23)" />
      </svg>
      <span>{value}</span>
    </div>
  );
}
