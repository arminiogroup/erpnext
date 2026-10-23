import type { DayPickerLocale } from 'react-day-picker';

type CalendarLocaleModule = Record<string, DayPickerLocale>;

const calendarLocaleModules = import.meta.glob<CalendarLocaleModule>([
    '/node_modules/react-day-picker/dist/esm/locale/*.js',
    '!/node_modules/react-day-picker/dist/esm/locale/*-jalali.js',
]);
const calendarLocaleLoaders = Object.fromEntries(
    Object.entries(calendarLocaleModules).map(([path, load]) => [path.toLowerCase(), load] as const)
);
const calendarLocaleAliases: Record<string, string> = {
    en: 'en-us', am: 'am-et', fa: 'fa-ir', no: 'nb', 'sr-cs': 'sr-latn', zh: 'zh-cn'
};
let calendarLocale: DayPickerLocale | undefined;

export const initializeCalendarLocale = async () => {
    let candidate = (window.frappe?.boot?.lang || '').toLowerCase().replace(/_/g, '-');
    while (candidate) {
        const code = calendarLocaleAliases[candidate] || candidate;
        const load = calendarLocaleLoaders[`/node_modules/react-day-picker/dist/esm/locale/${code}.js`];
        if (load) {
            let localeModule;
            try {
                localeModule = await load();
            } catch {
                return;
            }
            calendarLocale = Object.values(localeModule)[0];
            return;
        }
        candidate = candidate.includes('-') ? candidate.slice(0, candidate.lastIndexOf('-')) : '';
    }
};

export const getCalendarLocale = () => calendarLocale;
