// Turkish names for ECharts' time axis. The axis writes "14 Oct" from `{d} {MMM}`; ECharts takes
// the month names from its locale, and it ships English and Chinese only. A partial locale object
// is merged over English (`createLocaleObject`), so only the names the charts print are given.

export const TURKISH_ECHARTS_LOCALE = {
  time: {
    month: [
      "Ocak",
      "Şubat",
      "Mart",
      "Nisan",
      "Mayıs",
      "Haziran",
      "Temmuz",
      "Ağustos",
      "Eylül",
      "Ekim",
      "Kasım",
      "Aralık",
    ],
    monthAbbr: ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"],
    dayOfWeek: ["Pazar", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi"],
    dayOfWeekAbbr: ["Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"],
  },
} as const;
