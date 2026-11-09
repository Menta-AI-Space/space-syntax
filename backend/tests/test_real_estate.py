"""Unit tests for data.go.kr trade XML parsing (no live API)."""



import asyncio

from unittest.mock import AsyncMock, patch



import pytest
import httpx



from app.services.real_estate import (
    RealEstateConfigError,
    RealEstateError,
    compute_monthly_rent_per_sqm,
    compute_price_per_sqm,
    fetch_apartment_trades,
    fetch_commercial_trades,
    fetch_officetel_trades,
    parse_apartment_trades_xml,
    parse_officetel_rents_xml,
    parse_officetel_trades_xml,
    parse_trades_xml,
)



COMMERCIAL_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>

<response>

  <header>

    <resultCode>00</resultCode>

    <resultMsg>NORMAL SERVICE.</resultMsg>

  </header>

  <body>

    <items>

      <item>

        <dealAmount>40000</dealAmount>

        <buildingAr>120.5</buildingAr>

        <plottageAr>80.0</plottageAr>

        <umdNm>신촌동</umdNm>

        <jibun>123</jibun>

        <sggNm>서대문구</sggNm>

        <dealYear>2025</dealYear>

        <dealMonth>1</dealMonth>

        <dealDay>15</dealDay>

        <buildingUse>근린생활시설</buildingUse>

        <floor>1</floor>

        <buildYear>1998</buildYear>

        <buildingType>집합</buildingType>

        <landUse>일반상업</landUse>

        <shareDealingType></shareDealingType>

        <cdealType></cdealType>

        <cdealDay></cdealDay>

        <dealingGbn>중개거래</dealingGbn>

      </item>

      <item>

        <dealAmount>25000</dealAmount>

        <buildingAr>85</buildingAr>

        <plottageAr>60</plottageAr>

        <umdNm>창천동</umdNm>

        <jibun>45-2</jibun>

        <sggNm>서대문구</sggNm>

        <dealYear>2025</dealYear>

        <dealMonth>1</dealMonth>

        <dealDay>20</dealDay>

        <buildingUse>업무시설</buildingUse>

        <floor>2</floor>

        <buildYear>2005</buildYear>

        <buildingType>일반</buildingType>

        <landUse>제2종일반주거</landUse>

        <shareDealingType>지분</shareDealingType>

        <cdealType>O</cdealType>

        <cdealDay>25.02.01</cdealDay>

        <dealingGbn>직거래</dealingGbn>

      </item>

    </items>

    <numOfRows>100</numOfRows>

    <pageNo>1</pageNo>

    <totalCount>2</totalCount>

  </body>

</response>"""



APARTMENT_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>

<response>

  <header>

    <resultCode>00</resultCode>

    <resultMsg>NORMAL SERVICE.</resultMsg>

  </header>

  <body>

    <items>

      <item>

        <dealAmount>85000</dealAmount>

        <excluUseAr>84.92</excluUseAr>

        <aptNm>신촌푸르지오</aptNm>

        <umdNm>신촌동</umdNm>

        <jibun>50</jibun>

        <sggNm>서대문구</sggNm>

        <sggCd>11410</sggCd>

        <dealYear>2025</dealYear>

        <dealMonth>3</dealMonth>

        <dealDay>10</dealDay>

        <floor>12</floor>

        <buildYear>2010</buildYear>

        <dealingGbn>중개거래</dealingGbn>

      </item>

      <item>

        <dealAmount>62000</dealAmount>

        <excluUseAr>59.98</excluUseAr>

        <aptNm></aptNm>

        <umdNm>창천동</umdNm>

        <jibun>12-3</jibun>

        <sggNm>서대문구</sggNm>

        <sggCd>11410</sggCd>

        <dealYear>2025</dealYear>

        <dealMonth>2</dealMonth>

        <dealDay>5</dealDay>

        <floor>5</floor>

        <buildYear>2003</buildYear>

        <dealingGbn>직거래</dealingGbn>

        <cdealType>O</cdealType>

        <cdealDay>25.03.02</cdealDay>

      </item>

    </items>

    <numOfRows>100</numOfRows>

    <pageNo>1</pageNo>

    <totalCount>2</totalCount>

  </body>

</response>"""



OFFICETEL_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>

<response>

  <header>

    <resultCode>00</resultCode>

    <resultMsg>NORMAL SERVICE.</resultMsg>

  </header>

  <body>

    <items>

      <item>

        <dealAmount>55000</dealAmount>

        <excluUseAr>32.5</excluUseAr>

        <offiNm>신촌 오피스텔</offiNm>

        <umdNm>신촌동</umdNm>

        <jibun>88</jibun>

        <sggNm>서대문구</sggNm>

        <sggCd>11410</sggCd>

        <dealYear>2025</dealYear>

        <dealMonth>1</dealMonth>

        <dealDay>8</dealDay>

        <floor>7</floor>

        <buildYear>2015</buildYear>

        <dealingGbn>중개거래</dealingGbn>

      </item>

      <item>

        <dealAmount>32000</dealAmount>

        <excluUseAr>25.0</excluUseAr>

        <offiNm></offiNm>

        <umdNm>창천동</umdNm>

        <jibun>15-1</jibun>

        <sggNm>서대문구</sggNm>

        <sggCd>11410</sggCd>

        <dealYear>2025</dealYear>

        <dealMonth>2</dealMonth>

        <dealDay>14</dealDay>

        <floor>3</floor>

        <buildYear>2008</buildYear>

        <dealingGbn>직거래</dealingGbn>

      </item>

    </items>

    <numOfRows>100</numOfRows>

    <pageNo>1</pageNo>

    <totalCount>2</totalCount>

  </body>

</response>"""



OFFICETEL_RENT_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>

<response>

  <header>

    <resultCode>00</resultCode>

    <resultMsg>NORMAL SERVICE.</resultMsg>

  </header>

  <body>

    <items>

      <item>

        <deposit>10,000</deposit>

        <monthlyRent>90</monthlyRent>

        <excluUseAr>32.5</excluUseAr>

        <offiNm>신촌 오피스텔</offiNm>

        <umdNm>신촌동</umdNm>

        <jibun>88</jibun>

        <sggNm>서대문구</sggNm>

        <sggCd>11410</sggCd>

        <dealYear>2026</dealYear>

        <dealMonth>5</dealMonth>

        <dealDay>8</dealDay>

        <floor>7</floor>

        <buildYear>2015</buildYear>

        <contractType>갱신</contractType>

        <contractTerm>202605~202805</contractTerm>

        <useRRRight>사용</useRRRight>

        <preDeposit>10,000</preDeposit>

        <preMonthlyRent>80</preMonthlyRent>

      </item>

      <item>

        <deposit>20,000</deposit>

        <monthlyRent>0</monthlyRent>

        <excluUseAr>25.0</excluUseAr>

        <offiNm></offiNm>

        <umdNm>창천동</umdNm>

        <jibun>15-1</jibun>

        <sggNm>서대문구</sggNm>

        <sggCd>11410</sggCd>

        <dealYear>2026</dealYear>

        <dealMonth>4</dealMonth>

        <dealDay>14</dealDay>

        <floor>3</floor>

        <buildYear>2008</buildYear>

        <contractType>신규</contractType>

        <contractTerm>202604~202804</contractTerm>

        <useRRRight></useRRRight>

        <preDeposit>0</preDeposit>

        <preMonthlyRent>0</preMonthlyRent>

      </item>

    </items>

    <numOfRows>100</numOfRows>

    <pageNo>1</pageNo>

    <totalCount>2</totalCount>

  </body>

</response>"""



EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>

<response>

  <header>

    <resultCode>00</resultCode>

    <resultMsg>NORMAL SERVICE.</resultMsg>

  </header>

  <body>

    <items/>

    <numOfRows>100</numOfRows>

    <pageNo>1</pageNo>

    <totalCount>0</totalCount>

  </body>

</response>"""



ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>

<response>

  <header>

    <resultCode>03</resultCode>

    <resultMsg>NO DATA</resultMsg>

  </header>

</response>"""





def test_compute_price_per_sqm():

    assert compute_price_per_sqm(40000, 120.5) == pytest.approx(3_319_502.07, rel=1e-4)

    assert compute_price_per_sqm(40000, 0) is None

    assert compute_price_per_sqm(0, 100) is None





def test_parse_trades_xml_success():

    result = parse_trades_xml(COMMERCIAL_SAMPLE_XML)

    assert result.total_count == 2

    assert result.page_no == 1

    assert len(result.items) == 2



    first = result.items[0]

    assert first.deal_amount == 40000

    assert first.building_ar == 120.5

    assert first.umd_nm == "신촌동"

    assert first.jibun == "123"

    assert first.sgg_nm == "서대문구"

    assert first.property_type == "commercial"

    assert first.price_per_sqm == compute_price_per_sqm(40000, 120.5)

    assert first.geocode_query() == "서울 서대문구 신촌동 123번지"

    assert first.building_type == "집합"

    assert first.is_strata is True

    assert first.land_use == "일반상업"

    assert first.dealing_gbn == "중개거래"

    assert first.is_cancelled is False

    assert first.is_share_deal is False



    second = result.items[1]

    assert second.building_type == "일반"

    assert second.is_strata is False

    assert second.is_cancelled is True

    assert second.cdeal_day == "25.02.01"

    assert second.is_share_deal is True

    assert second.dealing_gbn == "직거래"





def test_parse_apartment_trades_xml_success():

    result = parse_apartment_trades_xml(APARTMENT_SAMPLE_XML)

    assert result.total_count == 2

    assert len(result.items) == 2



    first = result.items[0]

    assert first.deal_amount == 85000

    assert first.exclu_use_ar == 84.92

    assert first.apt_nm == "신촌푸르지오"

    assert first.property_type == "apartment"

    assert first.price_per_sqm == compute_price_per_sqm(85000, 84.92)

    assert first.geocode_query() == "서울 서대문구 신촌동 신촌푸르지오"



    assert first.is_cancelled is False



    second = result.items[1]

    assert second.apt_nm == ""

    assert second.geocode_query() == "서울 서대문구 창천동 12-3번지"

    assert second.is_cancelled is True

    assert second.cdeal_day == "25.03.02"





def test_parse_officetel_trades_xml_success():

    result = parse_officetel_trades_xml(OFFICETEL_SAMPLE_XML)

    assert result.total_count == 2

    assert len(result.items) == 2



    first = result.items[0]

    assert first.deal_amount == 55000

    assert first.exclu_use_ar == 32.5

    assert first.offi_nm == "신촌 오피스텔"

    assert first.property_type == "officetel"

    assert first.price_per_sqm == compute_price_per_sqm(55000, 32.5)

    assert first.geocode_query() == "서울 서대문구 신촌동 신촌 오피스텔"



    assert first.is_cancelled is False



    second = result.items[1]

    assert second.offi_nm == ""

    assert second.geocode_query() == "서울 서대문구 창천동 15-1번지"

    assert second.is_cancelled is False





def test_compute_monthly_rent_per_sqm():

    assert compute_monthly_rent_per_sqm(90, 32.5) == pytest.approx(27_692.31, rel=1e-4)

    assert compute_monthly_rent_per_sqm(0, 32.5) is None  # 전세

    assert compute_monthly_rent_per_sqm(90, 0) is None





def test_parse_officetel_rents_xml_success():

    result = parse_officetel_rents_xml(OFFICETEL_RENT_SAMPLE_XML)

    assert result.total_count == 2

    assert len(result.items) == 2



    first = result.items[0]

    assert first.deposit == 10_000

    assert first.monthly_rent == 90

    assert first.exclu_use_ar == 32.5

    assert first.property_type == "officetel_rent"

    assert first.is_jeonse is False

    assert first.is_renewal is True

    assert first.use_rr_right == "사용"

    assert first.pre_monthly_rent == 80

    assert first.contract_term == "202605~202805"

    assert first.monthly_rent_per_sqm == compute_monthly_rent_per_sqm(90, 32.5)

    assert first.geocode_query() == "서울 서대문구 신촌동 신촌 오피스텔"

    assert first.deal_date == "2026-05-08"



    second = result.items[1]

    assert second.monthly_rent == 0

    assert second.is_jeonse is True

    assert second.is_renewal is False

    assert second.monthly_rent_per_sqm is None

    assert second.deposit == 20_000

    assert second.geocode_query() == "서울 서대문구 창천동 15-1번지"





def test_parse_officetel_rents_xml_empty():

    result = parse_officetel_rents_xml(EMPTY_XML)

    assert result.total_count == 0

    assert result.items == []





def test_parse_officetel_rents_xml_api_error():

    with pytest.raises(RealEstateError, match="API error 03"):

        parse_officetel_rents_xml(ERROR_XML)





def test_parse_trades_xml_empty():

    result = parse_trades_xml(EMPTY_XML)

    assert result.total_count == 0

    assert result.items == []





def test_parse_apartment_trades_xml_empty():

    result = parse_apartment_trades_xml(EMPTY_XML)

    assert result.total_count == 0

    assert result.items == []





def test_parse_officetel_trades_xml_empty():

    result = parse_officetel_trades_xml(EMPTY_XML)

    assert result.total_count == 0

    assert result.items == []





def test_parse_trades_xml_api_error():

    with pytest.raises(RealEstateError, match="API error 03"):

        parse_trades_xml(ERROR_XML)





def test_parse_apartment_trades_xml_api_error():

    with pytest.raises(RealEstateError, match="API error 03"):

        parse_apartment_trades_xml(ERROR_XML)





def test_parse_officetel_trades_xml_api_error():

    with pytest.raises(RealEstateError, match="API error 03"):

        parse_officetel_trades_xml(ERROR_XML)





def test_parse_trades_xml_invalid():

    with pytest.raises(RealEstateError, match="Invalid XML"):

        parse_trades_xml("not xml")





def test_fetch_commercial_trades_mocked():

    mock_response = AsyncMock()

    mock_response.status_code = 200

    mock_response.text = COMMERCIAL_SAMPLE_XML

    mock_response.headers = {"content-type": "application/xml"}



    mock_client = AsyncMock()

    mock_client.get = AsyncMock(return_value=mock_response)

    mock_client.__aenter__ = AsyncMock(return_value=mock_client)

    mock_client.__aexit__ = AsyncMock(return_value=False)



    with patch("app.services.real_estate.settings") as mock_settings:

        mock_settings.data_go_api_key = "test-key"

        with patch("app.services.real_estate.httpx.AsyncClient", return_value=mock_client):

            result = asyncio.run(

                fetch_commercial_trades("11410", "202501")

            )



    assert len(result.items) == 2

    mock_client.get.assert_called_once()

    call_args = mock_client.get.call_args

    assert "getRTMSDataSvcNrgTrade" in call_args[0][0]

    assert call_args[1]["params"]["LAWD_CD"] == "11410"

    assert call_args[1]["params"]["DEAL_YMD"] == "202501"





def test_fetch_apartment_trades_mocked():

    mock_response = AsyncMock()

    mock_response.status_code = 200

    mock_response.text = APARTMENT_SAMPLE_XML

    mock_response.headers = {"content-type": "application/xml"}



    mock_client = AsyncMock()

    mock_client.get = AsyncMock(return_value=mock_response)

    mock_client.__aenter__ = AsyncMock(return_value=mock_client)

    mock_client.__aexit__ = AsyncMock(return_value=False)



    with patch("app.services.real_estate.settings") as mock_settings:

        mock_settings.data_go_api_key = "test-key"

        with patch("app.services.real_estate.httpx.AsyncClient", return_value=mock_client):

            result = asyncio.run(

                fetch_apartment_trades("11410", "202503")

            )



    assert len(result.items) == 2

    mock_client.get.assert_called_once()

    call_args = mock_client.get.call_args

    assert "getRTMSDataSvcAptTrade" in call_args[0][0]

    assert call_args[1]["params"]["LAWD_CD"] == "11410"

    assert call_args[1]["params"]["DEAL_YMD"] == "202503"





def test_fetch_officetel_trades_mocked():

    mock_response = AsyncMock()

    mock_response.status_code = 200

    mock_response.text = OFFICETEL_SAMPLE_XML

    mock_response.headers = {"content-type": "application/xml"}



    mock_client = AsyncMock()

    mock_client.get = AsyncMock(return_value=mock_response)

    mock_client.__aenter__ = AsyncMock(return_value=mock_client)

    mock_client.__aexit__ = AsyncMock(return_value=False)



    with patch("app.services.real_estate.settings") as mock_settings:

        mock_settings.data_go_api_key = "test-key"

        with patch("app.services.real_estate.httpx.AsyncClient", return_value=mock_client):

            result = asyncio.run(

                fetch_officetel_trades("11410", "202501")

            )



    assert len(result.items) == 2

    mock_client.get.assert_called_once()

    call_args = mock_client.get.call_args

    assert "getRTMSDataSvcOffiTrade" in call_args[0][0]

    assert call_args[1]["params"]["LAWD_CD"] == "11410"

    assert call_args[1]["params"]["DEAL_YMD"] == "202501"





def test_fetch_missing_api_key():

    with patch("app.services.real_estate.settings") as mock_settings:

        mock_settings.data_go_api_key = ""

        with pytest.raises(RealEstateConfigError, match="DATA_GO_API_KEY"):

            asyncio.run(fetch_commercial_trades("11410", "202501"))


def test_fetch_apartment_trades_wraps_http_transport_error():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ReadError("connection reset"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.real_estate.settings") as mock_settings:
        mock_settings.data_go_api_key = "test-key"
        with patch("app.services.real_estate.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RealEstateError, match="RTMS network request failed"):
                asyncio.run(fetch_apartment_trades("11410", "202503"))

