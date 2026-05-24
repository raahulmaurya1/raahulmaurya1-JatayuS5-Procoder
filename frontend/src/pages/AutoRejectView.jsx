import React from 'react';
import styles from './AutoRejectView.module.css';

export default function AutoRejectView({ sessionUlid }) {
  return (
    <div className={styles.wrapper}>
      {/* Header Banner */}
      <div className={styles.headerRed}>
        <div className={styles.iconWrap}>
          <svg width="80" height="80" viewBox="0 0 100 100">
            <circle className={styles.crossCircle} cx="50" cy="50" r="45" />
            <line className={`${styles.crossLine} ${styles.crossLine1}`} x1="35" y1="35" x2="65" y2="65" />
            <line className={`${styles.crossLine} ${styles.crossLine2}`} x1="65" y1="35" x2="35" y2="65" />
          </svg>
        </div>
        <h1 className={styles.title}>Application Auto Rejected</h1>
      </div>

      {/* Body */}
      <div className={styles.body}>
        <div className={styles.appCard}>
          <div className={styles.appCardLabel}>Application Number</div>
          <div className={styles.appCardValue}>{sessionUlid}</div>
        </div>

        <p className={styles.description}>
          We have detected high-risk activity beyond the permissible limit for your application. Therefore, we cannot proceed with your application.
        </p>
      </div>
    </div>
  );
}
