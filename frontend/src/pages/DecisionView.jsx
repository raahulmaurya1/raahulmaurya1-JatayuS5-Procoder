import React from 'react';
import styles from './DecisionView.module.css';

export default function DecisionView({ title, description, sessionUlid, extractedData = {} }) {
  const isApproved = title.toLowerCase().includes('approve');
  const isReview = title.toLowerCase().includes('review');

  // Format account type for display
  const formatAccountType = (type) => {
    if (!type) return '';
    const labels = {
      retail_savings: 'Retail Savings Account',
      sme_current: 'SME Current Account',
      digital_only: 'Digital Savings Account',
    };
    return labels[type] || type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  };

  const data = extractedData || {};
  const displayName = data.name || 'Valued Customer';
  const displayAccountType = formatAccountType(data.account_type);

  return (
    <div className={styles.wrapper}>
      {/* Header Banner */}
      <div className={`${styles.header} ${isApproved ? styles.headerGreen : styles.headerYellow}`}>
        <div className={styles.iconWrap}>
          {isApproved ? (
            <svg width="80" height="80" viewBox="0 0 100 100">
              <circle className={styles.tickCircle} cx="50" cy="50" r="45" />
              <polyline className={styles.tickCheck} points="28,52 44,68 72,34" />
            </svg>
          ) : (
            <svg width="80" height="80" viewBox="0 0 100 100">
              <polygon className={styles.warnTriangle} points="50,14 90,80 10,80" />
              <line className={styles.warnBang} x1="50" y1="36" x2="50" y2="62" />
              <circle className={styles.warnDot} cx="50" cy="71" r="3" />
            </svg>
          )}
        </div>
        <h1 className={styles.title}>{title}</h1>
        <p className={styles.subtitle}>{isApproved ? `Welcome to the family, ${displayName} 🎉` : "We're carefully reviewing your details"}</p>
      </div>

      {/* Body */}
      <div className={styles.body}>
        <p className={styles.greeting}>Dear {displayName},</p>
        {/* <p className={styles.description}>{description}</p> */}

        {isApproved && (
          <>
            <div className={styles.cardsGrid}>
              {/* Account Details Card */}
              <div className={styles.detailCard}>
                <div className={`${styles.detailCardTitle} ${styles.titleGreen}`}>Account Details</div>
                <div className={styles.detailRow}>
                  <span className={styles.detailLabel}>Account Number</span>
                  <span className={`${styles.detailValue} ${styles.accountNo}`}>{data.account_number || 'Pending'}</span>
                </div>
                <div className={styles.detailRow}>
                  <span className={styles.detailLabel}>Account Type</span>
                  <span className={styles.detailValue}>{displayAccountType}</span>
                </div>
                <div className={styles.detailRow}>
                  <span className={styles.detailLabel}>Application No.</span>
                  <span className={styles.detailValue} style={{ fontSize: '11px', letterSpacing: '0.5px' }}>{sessionUlid}</span>
                </div>
              </div>

              {/* Branch Details Card */}
              {(data.branch_name || data.pref_branch_ifsc) && (
                <div className={styles.detailCard}>
                  <div className={`${styles.detailCardTitle} ${styles.titleGreen}`}>Branch Details</div>
                  <div className={styles.detailRow}>
                    <span className={styles.detailLabel}>Branch Name</span>
                    <span className={styles.detailValue}>{data.branch_name}</span>
                  </div>
                  <div className={styles.detailRow}>
                    <span className={styles.detailLabel}>IFSC Code</span>
                    <span className={styles.detailValue} style={{ fontFamily: 'monospace' }}>{data.pref_branch_ifsc}</span>
                  </div>
                  {data.branch_address && (
                    <div className={styles.detailRow}>
                      <span className={styles.detailLabel}>Branch Address</span>
                      <span className={styles.detailValue}>{data.branch_address}</span>
                    </div>
                  )}
                </div>
              )}
            </div>

            <p className={styles.closingTextGreen}>
              Thank you for choosing us. Your financial journey begins now! 🚀
            </p>
          </>
        )}

        {isReview && (
          <>
            <div className={styles.appCard}>
              <div className={styles.appCardLabel}>Your Application Number</div>
              <div className={styles.appCardValue}>{sessionUlid}</div>
            </div>

            <div className={styles.steps}>
              <div className={styles.step}>
                <span className={styles.stepIcon}>🔍</span>
                <span className={styles.stepText}>
                  <strong>Review in progress</strong><br />
                  Our compliance team will review your documents within 2–3 business days.
                </span>
              </div>
              <div className={styles.step}>
                <span className={styles.stepIcon}>📧</span>
                <span className={styles.stepText}>
                  <strong>You will be notified</strong><br />
                  We will send you an email as soon as a decision is made.
                </span>
              </div>
            </div>

            <p className={styles.closingTextYellow}>
              Please keep your Application Number safe for future reference.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
