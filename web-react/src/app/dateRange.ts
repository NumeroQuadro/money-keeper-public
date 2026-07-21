export interface MonthRange {
  start: string;
  end: string;
}

export function monthRange(monthValue: string): MonthRange | null {
  if (!monthValue || !/^\d{4}-\d{2}$/.test(monthValue)) {
    return null;
  }

  const [yearRaw, monthRaw] = monthValue.split('-');
  const year = Number(yearRaw);
  const month = Number(monthRaw);

  if (!year || !month || month < 1 || month > 12) {
    return null;
  }

  const lastDay = new Date(year, month, 0).getDate();

  return {
    start: `${yearRaw}-${monthRaw}-01`,
    end: `${yearRaw}-${monthRaw}-${String(lastDay).padStart(2, '0')}`,
  };
}

export function monthFromDate(dateValue: string): string {
  if (!dateValue || !/^\d{4}-\d{2}-\d{2}$/.test(dateValue)) {
    return '';
  }

  return dateValue.slice(0, 7);
}

export function currentMonthValue(now: Date = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

export function monthRangeMonthsAgo(monthsAgo: number, now: Date = new Date()): MonthRange {
  const date = new Date(now.getFullYear(), now.getMonth() - monthsAgo, 1);
  const month = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
  return monthRange(month) as MonthRange;
}
