import React, { useState } from 'react';
import styles from './TrustSection.module.css';

const FINANCIAL_CARDS = [
  {
    id: 'deposit',
    icon: '🏦',
    title: 'Deposit Plan',
    subtitle: 'Calculator',
    color: 'primary',
  },
  {
    id: 'emi',
    icon: '📊',
    title: 'EMI Calculator',
    subtitle: 'Loan Planning',
    color: 'secondary',
  },
  {
    id: 'sip',
    icon: '📈',
    title: 'SIP Plan',
    subtitle: 'Investment Calculator',
    color: 'tertiary',
  },
];

// EMI Calculator Logic
const calculateEMI = (principal, rate, months) => {
  if (principal <= 0 || rate < 0 || months <= 0) return { emi: 0, total: 0 };
  const monthlyRate = rate / 100 / 12;
  if (monthlyRate === 0) {
    return { emi: principal / months, total: principal };
  }
  const emi = (principal * monthlyRate * Math.pow(1 + monthlyRate, months)) / 
              (Math.pow(1 + monthlyRate, months) - 1);
  return { emi: Math.round(emi * 100) / 100, total: Math.round(emi * months * 100) / 100 };
};

// Deposit Calculator Logic
const calculateDeposit = (principal, rate, years) => {
  if (principal <= 0 || rate < 0 || years <= 0) return { interest: 0, total: 0 };
  const interest = (principal * rate * years) / 100;
  return { interest: Math.round(interest * 100) / 100, total: Math.round((principal + interest) * 100) / 100 };
};

// SIP Calculator Logic
const calculateSIP = (monthlyAmount, expectedReturn, years) => {
  if (monthlyAmount <= 0 || expectedReturn < 0 || years <= 0) return { totalInvested: 0, maturity: 0, gains: 0 };
  const months = years * 12;
  const monthlyRate = expectedReturn / 100 / 12;
  const totalInvested = monthlyAmount * months;
  
  if (monthlyRate === 0) {
    return { totalInvested, maturity: totalInvested, gains: 0 };
  }
  
  const maturity = monthlyAmount * (((Math.pow(1 + monthlyRate, months) - 1) / monthlyRate) * (1 + monthlyRate));
  const gains = maturity - totalInvested;
  
  return { 
    totalInvested: Math.round(totalInvested * 100) / 100, 
    maturity: Math.round(maturity * 100) / 100, 
    gains: Math.round(gains * 100) / 100 
  };
};

const TESTIMONIALS = [
  {
    name: 'Priya Sharma',
    role: 'Small Business Owner, Delhi',
    text: 'Opened my current account in literally 90 seconds. The AI agent understood exactly what I needed. Absolutely blown away by the experience!',
    rating: 5,
    avatar: '👩‍💼',
  },
  {
    name: 'Rahul Verma',
    role: 'Software Engineer, Bangalore',
    text: 'Finally a bank that works for tech-savvy people. Zero paperwork, instant verification. The AI chat onboarding is genius.',
    rating: 5,
    avatar: '👨‍💻',
  },
  {
    name: 'Anjali Nair',
    role: 'Homemaker, Kochi',
    text: 'I was nervous about digital banking but Luna made it so easy. She guided me step by step. My account was ready before my chai got cold!',
    rating: 5,
    avatar: '👩',
  },
];

// EMI Calculator Component
function EMICalculator() {
  const [loanAmount, setLoanAmount] = useState(500000);
  const [interestRate, setInterestRate] = useState(9.55);
  const [tenure, setTenure] = useState(20);
  
  const months = tenure * 12;
  const { emi, total } = calculateEMI(loanAmount, interestRate, months);
  const interest = total - loanAmount;

  return (
    <div className={styles.calculatorContent}>
      <div className={styles.inputGroup}>
        <div className={styles.inputField}>
          <label>Loan Amount</label>
          <div className={styles.valueDisplay}>Rs.{(loanAmount / 100000).toFixed(2)}</div>
          <input 
            type="range" 
            min="10000" 
            max="10000000" 
            step="10000" 
            value={loanAmount} 
            onChange={(e) => setLoanAmount(Number(e.target.value))}
            className={styles.slider}
          />
          <div className={styles.rangeLabels}>
            <span>Rs.0.1L</span>
            <span>Rs.1Cr</span>
          </div>
        </div>

        <div className={styles.inputField}>
          <label>Interest Rate</label>
          <div className={styles.valueDisplay}>{interestRate.toFixed(2)} %</div>
          <input 
            type="range" 
            min="1" 
            max="30" 
            step="0.1" 
            value={interestRate} 
            onChange={(e) => setInterestRate(Number(e.target.value))}
            className={styles.slider}
          />
          <div className={styles.rangeLabels}>
            <span>1%</span>
            <span>30%</span>
          </div>
        </div>

        <div className={styles.inputField}>
          <label>Total Years</label>
          <div className={styles.valueDisplay}>{tenure} Years</div>
          <input 
            type="range" 
            min="1" 
            max="40" 
            step="1" 
            value={tenure} 
            onChange={(e) => setTenure(Number(e.target.value))}
            className={styles.slider}
          />
          <div className={styles.rangeLabels}>
            <span>1</span>
            <span>40</span>
          </div>
        </div>
      </div>

      <div className={styles.resultsGrid}>
        <div className={styles.resultItem}>
          <span className={styles.resultLabel}>Monthly EMI</span>
          <span className={styles.resultValue}>Rs. {(emi / 1000).toFixed(2)} K</span>
        </div>
        <div className={styles.resultItem}>
          <span className={styles.resultLabel}>Interest Payable</span>
          <span className={styles.resultValue}>Rs. {(interest / 100000).toFixed(2)} L</span>
        </div>
        <div className={styles.resultItem}>
          <span className={styles.resultLabel}>Total Amount Payable</span>
          <span className={styles.resultValue}>Rs. {(total / 100000).toFixed(2)} L</span>
        </div>
      </div>
    </div>
  );
}

// Deposit Calculator Component
function DepositCalculator() {
  const [principal, setPrincipal] = useState(500000);
  const [interestRate, setInterestRate] = useState(6.5);
  const [years, setYears] = useState(5);
  
  const { interest, total } = calculateDeposit(principal, interestRate, years);

  return (
    <div className={styles.calculatorContent}>
      <div className={styles.inputGroup}>
        <div className={styles.inputField}>
          <label>Principal Amount</label>
          <div className={styles.valueDisplay}>Rs.{(principal / 100000).toFixed(2)}</div>
          <input 
            type="range" 
            min="10000" 
            max="5000000" 
            step="10000" 
            value={principal} 
            onChange={(e) => setPrincipal(Number(e.target.value))}
            className={styles.slider}
          />
          <div className={styles.rangeLabels}>
            <span>Rs.0.1L</span>
            <span>Rs.50L</span>
          </div>
        </div>

        <div className={styles.inputField}>
          <label>Interest Rate (p.a.)</label>
          <div className={styles.valueDisplay}>{interestRate.toFixed(2)} %</div>
          <input 
            type="range" 
            min="2" 
            max="10" 
            step="0.1" 
            value={interestRate} 
            onChange={(e) => setInterestRate(Number(e.target.value))}
            className={styles.slider}
          />
          <div className={styles.rangeLabels}>
            <span>2%</span>
            <span>10%</span>
          </div>
        </div>

        <div className={styles.inputField}>
          <label>Tenure (Years)</label>
          <div className={styles.valueDisplay}>{years} Years</div>
          <input 
            type="range" 
            min="1" 
            max="10" 
            step="1" 
            value={years} 
            onChange={(e) => setYears(Number(e.target.value))}
            className={styles.slider}
          />
          <div className={styles.rangeLabels}>
            <span>1</span>
            <span>10</span>
          </div>
        </div>
      </div>

      <div className={styles.resultsGrid}>
        <div className={styles.resultItem}>
          <span className={styles.resultLabel}>Interest Earned</span>
          <span className={styles.resultValue}>Rs. {(interest / 100000).toFixed(2)} L</span>
        </div>
        <div className={styles.resultItem}>
          <span className={styles.resultLabel}>Principal Amount</span>
          <span className={styles.resultValue}>Rs. {(principal / 100000).toFixed(2)} L</span>
        </div>
        <div className={styles.resultItem}>
          <span className={styles.resultLabel}>Maturity Amount</span>
          <span className={styles.resultValue}>Rs. {(total / 100000).toFixed(2)} L</span>
        </div>
      </div>
    </div>
  );
}

// SIP Calculator Component
function SIPCalculator() {
  const [monthlyAmount, setMonthlyAmount] = useState(10000);
  const [expectedReturn, setExpectedReturn] = useState(12);
  const [years, setYears] = useState(10);
  
  const { totalInvested, maturity, gains } = calculateSIP(monthlyAmount, expectedReturn, years);

  return (
    <div className={styles.calculatorContent}>
      <div className={styles.inputGroup}>
        <div className={styles.inputField}>
          <label>Monthly Investment</label>
          <div className={styles.valueDisplay}>Rs.{(monthlyAmount / 1000).toFixed(1)}K</div>
          <input 
            type="range" 
            min="500" 
            max="100000" 
            step="500" 
            value={monthlyAmount} 
            onChange={(e) => setMonthlyAmount(Number(e.target.value))}
            className={styles.slider}
          />
          <div className={styles.rangeLabels}>
            <span>Rs.500</span>
            <span>Rs.1L</span>
          </div>
        </div>

        <div className={styles.inputField}>
          <label>Expected Return (p.a.)</label>
          <div className={styles.valueDisplay}>{expectedReturn.toFixed(2)} %</div>
          <input 
            type="range" 
            min="5" 
            max="25" 
            step="0.1" 
            value={expectedReturn} 
            onChange={(e) => setExpectedReturn(Number(e.target.value))}
            className={styles.slider}
          />
          <div className={styles.rangeLabels}>
            <span>5%</span>
            <span>25%</span>
          </div>
        </div>

        <div className={styles.inputField}>
          <label>Investment Period (Years)</label>
          <div className={styles.valueDisplay}>{years} Years</div>
          <input 
            type="range" 
            min="1" 
            max="40" 
            step="1" 
            value={years} 
            onChange={(e) => setYears(Number(e.target.value))}
            className={styles.slider}
          />
          <div className={styles.rangeLabels}>
            <span>1</span>
            <span>40</span>
          </div>
        </div>
      </div>

      <div className={styles.resultsGrid}>
        <div className={styles.resultItem}>
          <span className={styles.resultLabel}>Total Invested</span>
          <span className={styles.resultValue}>Rs. {(totalInvested / 100000).toFixed(2)} L</span>
        </div>
        <div className={styles.resultItem}>
          <span className={styles.resultLabel}>Estimated Gains</span>
          <span className={styles.resultValue}>Rs. {(gains / 100000).toFixed(2)} L</span>
        </div>
        <div className={styles.resultItem}>
          <span className={styles.resultLabel}>Maturity Amount</span>
          <span className={styles.resultValue}>Rs. {(maturity / 100000).toFixed(2)} L</span>
        </div>
      </div>
    </div>
  );
}

export default function TrustSection() {
  return (
    <section className={styles.section} id="trust">
      <div className={styles.container}>
        {/* Financial Planning Section */}
        <div className={styles.financialSection}>
          <div className={styles.header}>
            <span className={styles.sectionLabel}>Smart Banking</span>
            <h2 className={styles.title}>Financial Planning Made Easy</h2>
          </div>
          
          <div className={styles.financialGrid}>
            {FINANCIAL_CARDS.map((card) => (
              <div key={card.id} className={`${styles.financialCard} ${styles[`card_${card.color}`]}`}>
                <div className={styles.cardHeader}>
                  <div className={styles.cardIcon}>{card.icon}</div>
                  <div>
                    <h3 className={styles.cardTitle}>{card.title}</h3>
                    <p className={styles.cardSubtitle}>{card.subtitle}</p>
                  </div>
                </div>
                
                {card.id === 'emi' && <EMICalculator />}
                {card.id === 'deposit' && <DepositCalculator />}
                {card.id === 'sip' && <SIPCalculator />}
              </div>
            ))}
          </div>
        </div>

        {/* Header */}
        <div className={styles.header}>
          <span className={styles.sectionLabel}>Customer Stories</span>
          <h2 className={styles.title}>2 Million+ Happy Customers</h2>
        </div>

        {/* Testimonials */}
        <div className={styles.grid}>
          {TESTIMONIALS.map((t) => (
            <div key={t.name} className={styles.card}>
              <div className={styles.stars}>{'⭐'.repeat(t.rating)}</div>
              <p className={styles.text}>"{t.text}"</p>
              <div className={styles.author}>
                <div className={styles.avatar}>{t.avatar}</div>
                <div>
                  <div className={styles.name}>{t.name}</div>
                  <div className={styles.role}>{t.role}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
