import React, { useCallback, useEffect, useRef } from 'react';
import gsap from 'gsap';
import { TextPlugin } from 'gsap/TextPlugin';
import Globe3D from './Globe3D';
import styles from './HeroSection.module.css';

gsap.registerPlugin(TextPlugin);

const STATS = [
  {
    label: 'Customers Onboarded',
    initial: '0',
    target: 2000000,
    duration: 2,
    ease: 'power2.out',
    format: (val) => {
      const next = val / 1000000;
      return `${next.toFixed(next < 1 ? 1 : 0)}M+`;
    },
  },
  {
    label: 'Uptime Guarantee',
    initial: '0.0%',
    target: 99.9,
    duration: 2,
    ease: 'power2.out',
    format: (val) => `${val.toFixed(1)}%`,
  },
  {
    label: 'Account Opening',
    initial: '<0 min',
    target: 2,
    duration: 2,
    ease: 'power2.out',
    format: (val) => `<${Math.max(1, Math.round(val))} min`,
  },
];

export default function HeroSection({ onGetStarted }) {
  const decodeRef = useRef(null);
  const statRefs = useRef([]);
  const statTweensRef = useRef([]);

  const animateStat = useCallback((index) => {
    const stat = STATS[index];
    const el = statRefs.current[index];
    if (!stat || !el) return;

    statTweensRef.current[index]?.kill();
    el.textContent = stat.initial;

    const counter = { val: 0 };
    statTweensRef.current[index] = gsap.to(counter, {
      val: stat.target,
      duration: stat.duration,
      ease: stat.ease,
      onUpdate: () => {
        el.textContent = stat.format(counter.val);
      },
    });
  }, []);

  useEffect(() => {
    const tweenSlots = statTweensRef.current;

    const ctx = gsap.context(() => {
      const tl = gsap.timeline({ repeat: -1, repeatDelay: 0.8 });

      tl.fromTo(
        decodeRef.current,
        { text: '' },
        {
          text: { value: 'FINGERTIPS', speed: 0.4 },
          duration: 2,
          ease: 'none',
        }
      )
        .to({}, { duration: 0.8 });

      STATS.forEach((_, index) => animateStat(index));
    }, decodeRef);

    return () => {
      tweenSlots.forEach((tween) => tween?.kill());
      ctx.revert();
    };
  }, [animateStat]);

  return (
    <section className={styles.hero} id="hero">
      {/* Decorative background blobs */}
      <div className={styles.blob1} />
      <div className={styles.blob2} />

      <div className={styles.grid}>
        {/* ── LEFT COLUMN ── */}
        <div className={styles.left}>
         
          {/* Heading */}
          <h1 className={styles.heading}>
            <span className={styles.headingLine}>BANKING  AT YOUR</span>
            <span ref={decodeRef} className={styles.headingGold}>
              FINGERTIPS
            </span>
          </h1>

          {/* Sub-heading */}
          <p className={styles.subHeading}>
            Experience seamless account opening and onboarding
            <br />
            with Agentic AI
          </p>

          {/* CTA buttons */}
          <div className={styles.ctas}>
            <button className={styles.btnPrimary} onClick={onGetStarted}>
              Get Started
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path
                  d="M3 8H13M13 8L9 4M13 8L9 12"
                  stroke="#1a1a2e"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </button>
<button
  className={styles.btnOutline}
  onClick={() => window.open("https://youtu.be/OO37AeUx14Q", "_blank")}
>
  How it works ?
</button>          </div>

          {/* Stats */}
          <div className={styles.stats}>
            {STATS.map((s, index) => (
              <div
                key={s.label}
                className={styles.statItem}
                onMouseEnter={() => animateStat(index)}
              >
                <div
                  className={styles.statNum}
                  ref={(el) => {
                    statRefs.current[index] = el;
                  }}
                >
                  {s.initial}
                </div>
                <div className={styles.statLabel}>{s.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* ── RIGHT COLUMN – 3D Globe ── */}
        <div className={styles.right}>
          <div className={styles.globeGlow} />
          <Globe3D />
        </div>
      </div>
    </section>
  );
}