import React, { useEffect, useState } from 'react';
import styles from './ChatbotOrchestrator.module.css';

const STATUS_WORDS = [
  'Parsing your document…',
  'Extracting elements…',
  'Extracting text…',
  'Verifying details…',
  'Cross-checking data…',
  'Almost done…',
];

export default function ProcessingView() {
  const [statusWordIndex, setStatusWordIndex] = useState(0);
  // wordKey forces re-mount of <span> so the CSS fade-in animation replays each cycle
  const [wordKey, setWordKey] = useState(0);

  // Cycle through status words every 2s — same interval and logic as KycUploadView
  useEffect(() => {
    const interval = window.setInterval(() => {
      setStatusWordIndex((prev) => {
        const next = (prev + 1) % STATUS_WORDS.length;
        setWordKey((k) => k + 1);
        return next;
      });
    }, 2000);
    return () => window.clearInterval(interval);
  }, []);

  return (
    <div className={`${styles.viewCard} ${styles.kycProcessingOverlay}`} role="status" aria-live="polite">

      {/* Dual counter-rotating spinner rings + pulsing document icon */}
      <div className={styles.kycProcessingRing}>
        <div className={styles.kycProcessingIconInner} aria-hidden="true">
          <svg
            width="20" height="20" viewBox="0 0 24 24"
            fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          >
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
            <polyline points="10 9 9 9 8 9" />
          </svg>
        </div>
      </div>

      {/* Title + animated cycling status word + subtitle */}
      <div className={styles.kycProcessingTexts}>
        <h2 className={styles.kycProcessingTitle}>Processing Documents</h2>
        <div className={styles.kycStatusWordWrap}>
          <span key={wordKey} className={styles.kycStatusWord}>
            {STATUS_WORDS[statusWordIndex]}
          </span>
        </div>
        <p className={styles.kycProcessingMeta}>
          This may take a few moments — please don't close this window.
        </p>
      </div>

      {/* Bouncing gradient dots */}
      <div className={styles.kycDots} aria-hidden="true">
        <div className={styles.kycDot} />
        <div className={styles.kycDot} />
        <div className={styles.kycDot} />
      </div>

    </div>
  );
}
