"""
A snapshot of S&P 500 constituent tickers, used by get_sp500_batch to give
the autonomous agent systematic, guaranteed coverage of the actual index —
not just whatever shows up in raw volume/movers screeners, which skew
heavily toward penny stocks and rarely surface genuine S&P 500 names at all.

This is a point-in-time snapshot, not a live feed — index membership
changes periodically (additions, removals, mergers). It doesn't need to be
perfectly exact to be useful: the goal is broad, systematic coverage of
real, liquid, optionable large-caps, not an authoritative index record.
Refresh periodically if maintained long-term.
"""

SP500_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK.B", "AVGO", "TSLA",
    "LLY", "JPM", "V", "UNH", "XOM", "MA", "PG", "JNJ", "HD", "COST",
    "MRK", "ABBV", "CVX", "AMD", "CRM", "NFLX", "PEP", "KO", "ADBE", "WMT",
    "BAC", "TMO", "MCD", "CSCO", "ACN", "LIN", "ABT", "WFC", "DHR", "PM",
    "GE", "TXN", "INTU", "IBM", "CAT", "VZ", "AMGN", "NOW", "ISRG", "QCOM",
    "SPGI", "NEE", "UNP", "PFE", "AMAT", "DIS", "CMCSA", "RTX", "LOW", "HON",
    "UPS", "GS", "BKNG", "T", "ELV", "SYK", "AXP", "MS", "DE", "BLK",
    "LMT", "MDT", "TJX", "SCHW", "ADP", "MDLZ", "GILD", "VRTX", "REGN", "CI",
    "PLD", "ADI", "SBUX", "MMC", "CB", "AMT", "BSX", "PANW", "ETN", "SO",
    "BX", "ZTS", "MU", "FI", "DUK", "SLB", "PGR", "NOC", "SNPS", "APH",
    "CDNS", "BDX", "ICE", "WM", "TGT", "ITW", "CME", "EOG", "AON", "CSX",
    "MO", "GD", "FDX", "EQIX", "HUM", "SHW", "USB", "CL", "APD", "MCK",
    "PNC", "MSI", "PYPL", "MPC", "ORLY", "MAR", "PSX", "NSC", "EMR", "CCI",
    "TFC", "ROP", "OXY", "CMG", "AJG", "PXD", "DXCM", "MCO", "FCX", "TT",
    "ADSK", "SRE", "AZO", "NXPI", "CARR", "PSA", "MMM", "AEP", "AIG", "F",
    "GM", "NUE", "COF", "MET", "TRV", "SPG", "D", "KLAC", "ROST", "EXC",
    "CTAS", "PCAR", "HES", "OKE", "STZ", "KHC", "CTVA", "AFL", "IDXX", "MSCI",
    "DOW", "YUM", "ODFL", "A", "PRU", "GIS", "KMB", "CHTR", "ALL", "HAL",
    "VLO", "BK", "PAYX", "SYY", "AME", "CPRT", "WELL", "CMI", "IQV", "ON",
    "OTIS", "HSY", "FTNT", "PWR", "VRSK", "EW", "KR", "DHI", "BIIB", "LHX",
    "EA", "GEHC", "DD", "ANSS", "GWW", "FAST", "PEG", "ACGL", "ED", "GPN",
    "XEL", "WMB", "NDAQ", "APTV", "CDW", "WEC", "ROK", "TSCO", "URI", "RSG",
    "MTD", "FANG", "VMC", "CBRE", "MLM", "DVN", "HPQ", "EIX", "AWK", "CTSH",
    "ILMN", "DAL", "FICO", "EFX", "ES", "WTW", "STT", "TROW", "ETR", "GLW",
    "ZBH", "DLTR", "ULTA", "CAH", "HIG", "RMD", "ALGN", "FTV", "AVB", "TDY",
    "MTB", "DTE", "BR", "PPG", "K", "IFF", "CHD", "STE", "ANET", "LYB",
    "EBAY", "WBD", "VTR", "WY", "PPL", "TYL", "HPE", "CINF", "EXR", "COO",
    "ARE", "CNP", "PODD", "SBAC", "BALL", "CFG", "HBAN", "SYF", "LVS", "INVH",
    "NTRS", "FE", "MOH", "OMC", "AEE", "MAA", "RF", "ATO", "STLD", "BAX",
    "CMS", "PKI", "ESS", "KEY", "NTAP", "IEX", "ZBRA", "TER", "EXPD", "MKC",
    "L", "CE", "AKAM", "SWKS", "HOLX", "TRMB", "MRO", "J", "DGX", "PFG",
    "JBHT", "CAG", "NDSN", "CLX", "DRI", "AVY", "TXT", "IP", "WAT", "SJM",
    "POOL", "BXP", "MAS", "EPAM", "TFX", "JKHY", "AOS", "NI", "GEN", "LKQ",
    "APA", "PNR", "HST", "WRB", "EMN", "CPT", "VTRS", "CRL", "SWK", "PAYC",
    "KMX", "WBA", "HRL", "BBY", "IPG", "FMC", "TAP", "AAL", "CZR", "UHS",
    "GNRC", "NCLH", "CTLT", "RHI", "AIZ", "PNW", "MOS", "WYNN", "FRT", "HAS",
    "NWSA", "NWS", "BWA", "MHK", "DVA", "ALB", "RL", "BEN", "GL", "FOXA",
    "FOX", "MTCH", "PARA", "CCL", "RCL", "NRG", "LNC", "IVZ", "SEE", "VFC",
]
