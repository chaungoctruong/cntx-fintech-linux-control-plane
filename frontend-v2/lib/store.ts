import { create } from "zustand";

export interface TransactionItem {
  id: number | string;
  type: string;
  amount: number;
  status: string;
  created_at: string;
  tx_ref?: string;
}

export interface LeaderboardEntry {
  rank: number;
  referral_count: number;
  masked_username: string;
}

export interface BonusEventItem {
  id: number;
  amount: number;
  reason: string;
  created_at: string;
}

interface AppState {
  balance: number;
  equity: number;
  username: string | null;
  depositAddress: string;
  transactions: TransactionItem[];
  referralLink: string;
  totalReferrals: number;
  bonusEarned: number;
  leaderboard: LeaderboardEntry[];
  bonusEvents: BonusEventItem[];
  setBalance: (v: number) => void;
  setEquity: (v: number) => void;
  setUsername: (v: string | null) => void;
  setDepositAddress: (v: string) => void;
  setTransactions: (v: TransactionItem[]) => void;
  setReferralLink: (v: string) => void;
  setTotalReferrals: (v: number) => void;
  setBonusEarned: (v: number) => void;
  setLeaderboard: (v: LeaderboardEntry[]) => void;
  setBonusEvents: (v: BonusEventItem[]) => void;
  toast: string | null;
  setToast: (v: string | null) => void;
}

const createSetter =
  <K extends keyof AppState>(key: K) =>
  (value: AppState[K]) =>
    ({ [key]: value } as Pick<AppState, K>);

export const useAppStore = create<AppState>((set) => ({
  balance: 0,
  equity: 0,
  username: null,
  depositAddress: "",
  transactions: [],
  referralLink: "",
  totalReferrals: 0,
  bonusEarned: 0,
  leaderboard: [],
  bonusEvents: [],
  setBalance: (v) => set(createSetter("balance")(v)),
  setEquity: (v) => set(createSetter("equity")(v)),
  setUsername: (v) => set(createSetter("username")(v)),
  setDepositAddress: (v) => set(createSetter("depositAddress")(v)),
  setTransactions: (v) => set(createSetter("transactions")(v)),
  setReferralLink: (v) => set(createSetter("referralLink")(v)),
  setTotalReferrals: (v) => set(createSetter("totalReferrals")(v)),
  setBonusEarned: (v) => set(createSetter("bonusEarned")(v)),
  setLeaderboard: (v) => set(createSetter("leaderboard")(v)),
  setBonusEvents: (v) => set(createSetter("bonusEvents")(v)),
  toast: null,
  setToast: (v) => set(createSetter("toast")(v)),
}));
