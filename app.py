import logging
import streamlit as st
try:
 from entry_points.view import render
 render()
except Exception as error:
 # No credentials are used by this app. Do not show URLs or source contents.
 from entry_points import diagnostics
 diagnostics.error(error,operation='entry_points.render')
 st.error('Не удалось открыть проверенный набор «Точки входа». Предыдущие данные не заменяются. Подробности сохранены в серверном журнале.')
