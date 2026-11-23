"""參考實作：由 data/taiex.csv + data/margin_daily.csv 計算每週 熱度(pct) 與 冷度(mdd)。
用法：python compute_gauge.py <taiex.csv> <margin_daily.csv> <golden.csv>
會與 golden.csv 的 pct、mdd 逐週比對並印出最大誤差。"""
import sys, numpy as np, pandas as pd
idx=pd.read_csv(sys.argv[1],parse_dates=["date"]); mg=pd.read_csv(sys.argv[2],parse_dates=["date"])
d=idx.merge(mg[["date","mrg_amt"]],on="date",how="left"); d["wk"]=d.date.dt.to_period("W")
g=d.groupby("wk")
wk=pd.DataFrame({"date":g.date.max(),"close":g.close.last(),"mrg_amt":g.mrg_amt.last()}).reset_index(drop=True)
wk=wk[wk.date>="2007-01-01"].reset_index(drop=True)
wk["mrg_amt"]=wk.mrg_amt.interpolate(limit=2); wk=wk.dropna(subset=["mrg_amt"]).reset_index(drop=True)
lm=np.log(wk.mrg_amt)
wk["pct"]=100*lm.rolling(156,min_periods=104).apply(lambda x:(x[:-1]<x[-1]).mean(),raw=True)
wk["mdd"]=100*(np.exp(lm-lm.rolling(52,min_periods=26).max())-1)
wk["state"]=np.where(wk.pct>=90,"過熱提醒",np.where(wk.mdd<=-30,"過冷提醒","中性"))
print(wk.tail(3)[["date","close","mrg_amt","pct","mdd","state"]].to_string(index=False))
if len(sys.argv)>3:
    gd=pd.read_csv(sys.argv[3],parse_dates=["date"]).set_index("date")
    m=wk.set_index("date").join(gd[["pct","mdd"]],rsuffix="_g",how="inner")
    ep=(m.pct-m.pct_g).abs().max(); em=(m.mdd-m.mdd_g).abs().max()
    print(f"比對週數={len(m)} pct最大誤差={ep:.6f} mdd最大誤差={em:.6f}"); sys.exit(0 if max(ep,em)<0.01 else 1)
