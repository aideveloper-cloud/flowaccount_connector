# FlowAccount Connector (ERPNext → FlowAccount)

ดันใบเสนอราคา (Quotation) จาก ERPNext เข้า FlowAccount อัตโนมัติเมื่อกด Submit
ติดตั้งบน Frappe Cloud ได้ **โดยไม่ต้องมี dev environment / ไม่ต้องแตะ terminal**

---

## ขั้นตอนแบบผ่านเว็บล้วน (ไม่มี terminal)

### ขั้นที่ 1 — เอาโค้ดขึ้น GitHub
1. สมัคร/ล็อกอิน GitHub (ฟรี)
2. กด **New repository** ตั้งชื่อ `flowaccount_connector` เลือก Public แล้ว Create
3. ในหน้า repo กด **Add file → Upload files** แล้วลาก **เนื้อหาทั้งหมดในโฟลเดอร์นี้**
   (ตัวไฟล์ `pyproject.toml`, `license.txt`, และโฟลเดอร์ `flowaccount_connector/`)
   เข้าไปวาง แล้ว Commit
   > ต้องให้ `pyproject.toml` อยู่ที่ "ราก" ของ repo

### ขั้นที่ 2 — เพิ่ม app เข้า Bench บน Frappe Cloud
1. dashboard → **Benches** → เลือก bench ที่ site ของคุณใช้อยู่
2. แท็บ **Apps → Add App** → เลือกแบบ **From GitHub / Public URL**
3. วาง URL repo ของคุณ + branch `main` → Add
4. กด **Deploy / Update** รอบิลด์เสร็จ
   > ถ้าหน้านี้ให้เลือกได้แค่ app จาก marketplace ไม่มีช่องใส่ GitHub URL
   > แปลว่าแพลน/bench เป็นแบบ shared ที่เพิ่ม custom app ไม่ได้ —
   > กรณีนี้ให้บอกผม จะเปลี่ยนไปวิธี Server Script (วางโค้ดในเว็บแทน)

### ขั้นที่ 3 — Install ลง site
dashboard → **Sites → kgarden.s.frappe.cloud → Apps → Install App**
→ เลือก `flowaccount_connector`
(custom field 2 ตัวบน Quotation จะถูกสร้างให้อัตโนมัติ ไม่ต้องไปเพิ่มเอง)

### ขั้นที่ 4 — ใส่ credential ผ่าน Site Config
dashboard → **Sites → ... → Site Config** เพิ่ม key เหล่านี้:

| Key                         | Value                                    |
|-----------------------------|------------------------------------------|
| `flowaccount_enabled`       | `1`                                      |
| `flowaccount_client_id`     | (client id ของคุณ)                       |
| `flowaccount_client_secret` | (client secret ตัวใหม่ที่ regenerate แล้ว) |

> ใช้ secret **ตัวใหม่** ที่ regenerate หลังจากที่ตัวเดิมเคยถูกแชร์ออกไป

### ขั้นที่ 5 — ทดสอบ
สร้างใบเสนอราคาทดสอบ 1 ใบ แล้ว Submit → ดูว่า field "FlowAccount Document"
มีเลขเด้งขึ้น และเอกสารโผล่ใน FlowAccount (สถานะ awaiting/รออนุมัติ)
ถ้าพลาด ดูที่ **Error Log** ใน ERPNext — มีข้อความจาก FlowAccount + payload ที่ส่งไป

---

## VAT / no-VAT
ถ้าใบเสนอราคามีบรรทัดภาษี → ส่ง `isVat: true` (มี 7%)
ถ้าไม่มี → `isVat: false` (no-VAT) อัตโนมัติ
(ใช้ร่วมกับ Tax Template ของ ERPNext Thailand ได้เลย)

## โครงสร้างไฟล์
```
pyproject.toml                       # ต้องอยู่ที่ราก repo
license.txt
flowaccount_connector/
  hooks.py                           # ผูก event on_submit ของ Quotation
  modules.txt  patches.txt
  fixtures/custom_field.json         # สร้าง custom field อัตโนมัติตอน install
  flowaccount/
    client.py                        # token (cache) + create_quotation
    mapping.py                       # Quotation -> payload FlowAccount
    events.py                        # background job ตอน submit
```

## ปรับแต่งภายหลัง (แก้ใน mapping.py)
- `creditType` ตอนนี้ = 3 (เงินสด) ถ้าให้เครดิตเปลี่ยนเป็น 1 + ใส่ `creditDays`
- `_item_type()` map จาก is_stock_item — ปรับให้ตรงสินค้าจริงได้
- ต่อยอด sync ใบกำกับ/ใบเสร็จ ใช้ pattern เดียวกัน เปลี่ยน endpoint + payload
