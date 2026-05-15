# MT5 Broker Notes

Quy tac chung khi tra loi ve broker MT5:
- Khong bia spread, swap, commission, margin, leverage neu chua co server/account type/symbol specification that.
- Yeu cau anh Contract Specification hoac thong tin broker/server/account type khi user hoi chi phi cu the.
- Khi loi login, kiem tra dung server MT5, dung login MT5, va mat khau trading.
- Khi broker reject order, can retcode/order_send log, symbol suffix, min lot, lot step, stop level, freeze level, trade mode, spread/slippage.

## Exness

Exness co thong so phu thuoc server, loai tai khoan va symbol suffix.

- Swap, spread, commission va leverage co the khac giua Standard, Raw/Zero/Pro va tung server.
- Khi loi login, kiem tra dung server Exness-MT5 that va khong nham login MT5 voi email/Personal Area.
- Neu vua doi password trading, can dang nhap kiem tra lai account.

## IC Markets

IC Markets co nhieu server MT5 va nhieu loai tai khoan.

- Khong lay thong so IC Markets chung de tinh phi cho user neu chua co account type/server.
- Raw Spread can kiem tra commission rieng.
- Swap qua dem can swap long/short trong Contract Specification.
- Khi bot khong vao lenh, kiem tra symbol suffix, minimum lot/step lot, trade mode, spread/slippage o phien tin manh.

## XM

XM co nhieu nhom tai khoan va symbol co the khac ten/suffix theo server.

- Hoi dung server MT5, login va loai tai khoan.
- Neu loi invalid volume hoac invalid stops, kiem tra lot step, min lot, stop level va freeze level trong Contract Specification.
- Bot chi nen chay sau khi account da connected/login-ready.
- Neu live khac demo, uu tien kiem tra spread/slippage/server truoc.

## Vantage

Vantage MT5 co thong so phu thuoc server, account type va symbol suffix.

- Can Contract Specification de tinh margin/swap/commission.
- Khong dung so mau hoac so broker khac de tra loi.
- Kiem tra dung server, login MT5, quyen trading cua account.
- Neu broker reject order, can retcode/order_send log de khoanh nguyen nhan.
